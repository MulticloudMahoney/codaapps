import asyncio
import aiohttp
from typing import List, Dict, Optional, Union
from bs4 import BeautifulSoup
import trafilatura
from dataclasses import dataclass
import logging
import re

logger = logging.getLogger(__name__)

@dataclass
class BatchProgress:
    total_urls: int
    processed_urls: int = 0
    failed_urls: int = 0
    current_batch: int = 0
    total_batches: int = 0

class BatchProcessor:
    def __init__(self, batch_size: int = 10, max_concurrent: int = 5):
        self.batch_size = batch_size
        self.max_concurrent = max_concurrent
        self.progress = None
        
    async def process_url(self, session: aiohttp.ClientSession, url: str, 
                         content_selector: Optional[Union[str, List[str]]] = None,
                         code_selector: Optional[str] = None,
                         exclude_selector: Optional[str] = None,
                         search_query: Optional[str] = None,
                         content_filters: Optional[Dict[str, str]] = None) -> Dict[str, str]:
        try:
            async with session.get(url) as response:
                if response.status == 200:
                    html_content = await response.text()
                    
                    # Use trafilatura for initial content extraction
                    downloaded = html_content
                    if content_selector or code_selector or exclude_selector:
                        soup = BeautifulSoup(downloaded, 'html.parser')
                        content = []
                        
                        # Process content selectors
                        if content_selector:
                            selectors = [content_selector] if isinstance(content_selector, str) else content_selector
                            for selector in selectors:
                                elements = soup.select(selector)
                                content.extend([elem.get_text(separator=' ', strip=True) 
                                            for elem in elements if elem.get_text(strip=True)])
                        
                        # Process code selector
                        if code_selector:
                            code_elements = soup.select(code_selector)
                            content.extend([elem.get_text(separator='\n', strip=True) 
                                        for elem in code_elements if elem.get_text(strip=True)])
                        
                        # Process exclude selector
                        if exclude_selector:
                            for elem in soup.select(exclude_selector):
                                elem.decompose()
                                
                        text_content = "\n\n".join(content) if content else "No content found"
                    else:
                        text_content = trafilatura.extract(downloaded) or "No content found"

                    # Apply search query if provided
                    if search_query and text_content != "No content found":
                        if not re.search(re.escape(search_query), text_content, re.IGNORECASE):
                            return {url: "Content filtered: Does not match search query"}

                    # Apply content filters if provided
                    if content_filters and text_content != "No content found":
                        for filter_type, filter_value in content_filters.items():
                            if filter_type == "min_length" and len(text_content) < int(filter_value):
                                return {url: f"Content filtered: Too short (min: {filter_value})"}
                            elif filter_type == "max_length" and len(text_content) > int(filter_value):
                                return {url: f"Content filtered: Too long (max: {filter_value})"}
                            elif filter_type == "contains" and filter_value.lower() not in text_content.lower():
                                return {url: f"Content filtered: Does not contain '{filter_value}'"}
                            elif filter_type == "excludes" and filter_value.lower() in text_content.lower():
                                return {url: f"Content filtered: Contains excluded text '{filter_value}'"}

                    return {url: text_content}
                else:
                    return {url: f"Error: HTTP {response.status}"}
        except Exception as e:
            logger.error(f"Error processing URL {url}: {str(e)}")
            return {url: f"Error: {str(e)}"}
        finally:
            if self.progress:
                self.progress.processed_urls += 1

    async def process_batch(self, urls: List[str], **kwargs) -> Dict[str, str]:
        async with aiohttp.ClientSession() as session:
            tasks = [self.process_url(session, url, **kwargs) for url in urls]
            results = await asyncio.gather(*tasks)
            
            # Merge results
            merged_results = {}
            for result in results:
                merged_results.update(result)
            
            if self.progress:
                self.progress.current_batch += 1
                
            return merged_results

    async def process_urls(self, urls: List[str], **kwargs) -> Dict[str, str]:
        # Initialize progress tracking
        self.progress = BatchProgress(
            total_urls=len(urls),
            total_batches=len(urls) // self.batch_size + (1 if len(urls) % self.batch_size else 0)
        )
        
        results = {}
        for i in range(0, len(urls), self.batch_size):
            batch = urls[i:i + self.batch_size]
            batch_results = await self.process_batch(batch, **kwargs)
            results.update(batch_results)
            
        return results

def process_urls_sync(urls: List[str], **kwargs) -> Dict[str, str]:
    processor = BatchProcessor()
    return asyncio.run(processor.process_urls(urls, **kwargs))
