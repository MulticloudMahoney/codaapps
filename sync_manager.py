import asyncio
import hashlib
import json
from datetime import datetime, timedelta
import logging
import os
import psycopg2
from psycopg2.extras import DictCursor
from typing import List, Dict, Optional
from .batch_processor import BatchProcessor

logger = logging.getLogger(__name__)

class SyncManager:
    def __init__(self):
        self.db_url = os.environ['DATABASE_URL']
        self.batch_processor = BatchProcessor()
        logger.info("SyncManager initialized")
        
    def _get_db_connection(self):
        return psycopg2.connect(self.db_url)
        
    def create_sync_config(self, doc_id: str, base_page_name: str, sync_interval: int,
                          content_selector: Optional[str] = None,
                          code_selector: Optional[str] = None,
                          exclude_selector: Optional[str] = None) -> int:
        logger.info(f"Creating sync config for doc_id: {doc_id}")
        with self._get_db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO sync_configurations 
                    (doc_id, base_page_name, content_selector, code_selector, 
                     exclude_selector, sync_interval)
                    VALUES (%s, %s, %s, %s, %s, %s)
                    RETURNING id
                """, (doc_id, base_page_name, content_selector, code_selector,
                      exclude_selector, sync_interval))
                config_id = cur.fetchone()[0]
                conn.commit()
                logger.info(f"Created sync config with ID: {config_id}")
                return config_id
                
    def add_urls_to_config(self, config_id: int, urls: List[str]):
        logger.info(f"Adding {len(urls)} URLs to config {config_id}")
        with self._get_db_connection() as conn:
            with conn.cursor() as cur:
                for url in urls:
                    cur.execute("""
                        INSERT INTO tracked_urls (config_id, url)
                        VALUES (%s, %s)
                        ON CONFLICT (config_id, url) DO NOTHING
                    """, (config_id, url))
                conn.commit()
                logger.info(f"Added URLs to config {config_id}")
                
    def _compute_content_hash(self, content: str) -> str:
        return hashlib.sha256(content.encode()).hexdigest()
        
    async def _process_config(self, config: Dict):
        logger.info(f"Processing config {config['id']}")
        try:
            with self._get_db_connection() as conn:
                with conn.cursor(cursor_factory=DictCursor) as cur:
                    # Get URLs for this config
                    cur.execute("SELECT url, last_content_hash FROM tracked_urls WHERE config_id = %s", 
                              (config['id'],))
                    tracked_urls = cur.fetchall()
                    
                    if not tracked_urls:
                        logger.info(f"No URLs found for config {config['id']}")
                        return
                        
                    logger.info(f"Processing {len(tracked_urls)} URLs for config {config['id']}")
                    
                    # Process URLs
                    urls_to_process = [row['url'] for row in tracked_urls]
                    results = await self.batch_processor.process_urls(
                        urls_to_process,
                        content_selector=config['content_selector'],
                        code_selector=config['code_selector'],
                        exclude_selector=config['exclude_selector']
                    )
                    
                    # Check for changes and update
                    urls_processed = len(results)
                    urls_failed = sum(1 for content in results.values() 
                                    if content.startswith("Error:"))
                    urls_changed = 0
                    
                    for url, content in results.items():
                        if not content.startswith("Error:"):
                            new_hash = self._compute_content_hash(content)
                            old_hash = next((row['last_content_hash'] 
                                           for row in tracked_urls 
                                           if row['url'] == url), None)
                            
                            if new_hash != old_hash:
                                urls_changed += 1
                                logger.info(f"Content changed for URL: {url}")
                                
                                # Update tracked URL
                                cur.execute("""
                                    UPDATE tracked_urls 
                                    SET last_content_hash = %s, last_sync = NOW()
                                    WHERE config_id = %s AND url = %s
                                """, (new_hash, config['id'], url))
                                
                                # Generate new sub-page in Coda
                                try:
                                    from coda_pack import Formula_GenerateSubPage
                                    page_url = Formula_GenerateSubPage(
                                        config['doc_id'],
                                        f"{config['base_page_name']} - {datetime.now().strftime('%Y-%m-%d %H:%M')}",
                                        {url: content}
                                    )
                                    logger.info(f"Generated new Coda page: {page_url}")
                                except Exception as coda_error:
                                    logger.error(f"Error generating Coda page: {str(coda_error)}")
                    
                    # Update sync history with detailed status
                    status_message = (
                        f"Processed {urls_processed} URLs: "
                        f"{urls_changed} changed, {urls_failed} failed"
                    )
                    logger.info(f"Sync complete for config {config['id']}: {status_message}")
                    
                    cur.execute("""
                        INSERT INTO sync_history 
                        (config_id, status, message, urls_processed, urls_failed)
                        VALUES (%s, %s, %s, %s, %s)
                    """, (config['id'], 'completed', status_message,
                          urls_processed, urls_failed))
                    
                    # Update last sync time
                    cur.execute("""
                        UPDATE sync_configurations 
                        SET last_sync = NOW()
                        WHERE id = %s
                    """, (config['id'],))
                    
                    conn.commit()
                    
        except Exception as e:
            logger.error(f"Error processing config {config['id']}: {str(e)}")
            with self._get_db_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute("""
                        INSERT INTO sync_history 
                        (config_id, status, message, urls_processed, urls_failed)
                        VALUES (%s, %s, %s, %s, %s)
                    """, (config['id'], 'error', str(e), 0, 0))
                    conn.commit()
                    
    async def run_sync_cycle(self):
        logger.info("Starting sync cycle")
        try:
            with self._get_db_connection() as conn:
                with conn.cursor(cursor_factory=DictCursor) as cur:
                    # Get active configs that need syncing
                    cur.execute("""
                        SELECT * FROM sync_configurations
                        WHERE active = true
                        AND (
                            last_sync IS NULL
                            OR NOW() - last_sync > (sync_interval || ' minutes')::interval
                        )
                    """)
                    configs = cur.fetchall()
                    
                    if not configs:
                        logger.info("No configurations need syncing at this time")
                        return
                        
                    logger.info(f"Found {len(configs)} configs to sync")
                    
                    # Process each config
                    await asyncio.gather(*[self._process_config(dict(config)) 
                                         for config in configs])
        except Exception as e:
            logger.error(f"Error in sync cycle: {str(e)}")
                                      
    def start_sync_service(self):
        """Start the sync service to run continuously"""
        logger.info("Starting sync service")
        async def _run_service():
            while True:
                try:
                    await self.run_sync_cycle()
                except Exception as e:
                    logger.error(f"Error in sync service: {str(e)}")
                finally:
                    await asyncio.sleep(60)  # Check every minute
                    
        asyncio.run(_run_service())
