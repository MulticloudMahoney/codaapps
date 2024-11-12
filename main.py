import os
from flask import Flask, render_template, request, send_file, jsonify
from utils.batch_processor import process_urls_sync
from utils.sync_manager import SyncManager
import json
import tempfile
import markdown
from bs4 import BeautifulSoup
from urllib.parse import urlparse
import logging
import threading
import sys
import psycopg2
import psycopg2.extras

# Configure logging
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger(__name__)

app = Flask(__name__)
app.secret_key = os.urandom(24)

# Initialize sync manager
sync_manager = SyncManager()

# Start sync service in a separate thread
def run_sync_service():
    try:
        logger.info("Starting sync service thread...")
        sync_manager.start_sync_service()
    except Exception as e:
        logger.error(f"Sync service thread crashed: {str(e)}")
        # Restart the sync thread if it crashes
        logger.info("Attempting to restart sync service...")
        threading.Timer(60.0, run_sync_service).start()

sync_thread = threading.Thread(target=run_sync_service, daemon=True)
sync_thread.start()
logger.info("Sync service thread started successfully")

@app.route('/', methods=['GET', 'POST'])
def index():
    if request.method == 'POST':
        urls_input = request.form.get('urls', '').split('\n')
        output_format = request.form.get('output_format')
        
        # Get custom selectors
        content_selector_types = request.form.getlist('content_selector_type[]')
        custom_selector = request.form.get('content_selector', '').strip()
        
        # Get search and filter parameters
        search_query = request.form.get('search_query', '').strip() or None
        min_length = request.form.get('min_length', '').strip()
        max_length = request.form.get('max_length', '').strip()
        contains_text = request.form.get('contains_text', '').strip()
        excludes_text = request.form.get('excludes_text', '').strip()
        
        # Build content filters dictionary
        content_filters = {}
        if min_length and min_length.isdigit():
            content_filters['min_length'] = min_length
        if max_length and max_length.isdigit():
            content_filters['max_length'] = max_length
        if contains_text:
            content_filters['contains'] = contains_text
        if excludes_text:
            content_filters['excludes'] = excludes_text
        
        # Combine selected types with custom selector if present
        if 'custom' in content_selector_types and custom_selector:
            content_selector_types.remove('custom')
            if custom_selector:
                content_selector_types.append(custom_selector)
        
        # Join all selectors with commas
        content_selector = ', '.join(filter(None, content_selector_types)) if content_selector_types else None
        code_selector = request.form.get('code_selector', '').strip() or None
        exclude_selector = request.form.get('exclude_selector', '').strip() or None
        
        # Properly parse and validate URLs
        urls = []
        for url in urls_input:
            url = url.strip()
            if url:
                parsed_url = urlparse(url)
                if parsed_url.scheme and parsed_url.netloc:
                    urls.append(url)
        
        if not urls:
            return "No valid URLs provided", 400
            
        scraped_data = process_urls_sync(
            urls,
            content_selector=content_selector,
            code_selector=code_selector,
            exclude_selector=exclude_selector,
            search_query=search_query,
            content_filters=content_filters if content_filters else None
        )
        logger.debug(f"Scraped data: {scraped_data}")
        
        # Filter out error messages and filtered content
        valid_results = {url: content for url, content in scraped_data.items() 
                        if content and not content.startswith(('Error:', 'Content filtered:'))}
        
        if not valid_results:
            return "No content was extracted or all content was filtered out", 400

        try:
            if output_format == 'txt':
                with tempfile.NamedTemporaryFile(mode='w+', delete=False, suffix='.txt') as temp_file:
                    for url, content in valid_results.items():
                        temp_file.write(f"URL: {url}\n\n")
                        temp_file.write(content)
                        temp_file.write("\n\n" + "-"*50 + "\n\n")
                    temp_file.flush()
                    logger.info(f"Created TXT file: {temp_file.name}")
                    return send_file(temp_file.name, as_attachment=True, download_name='scraped_content.txt')
                    
            elif output_format == 'json':
                with tempfile.NamedTemporaryFile(mode='w+', delete=False, suffix='.json') as temp_file:
                    json.dump(valid_results, temp_file, indent=2)
                    temp_file.flush()
                    logger.info(f"Created JSON file: {temp_file.name}")
                    return send_file(temp_file.name, as_attachment=True, download_name='scraped_content.json')
                    
            elif output_format == 'jsonl':
                with tempfile.NamedTemporaryFile(mode='w+', delete=False, suffix='.jsonl') as temp_file:
                    for url, content in valid_results.items():
                        code_blocks = [block for block in content.split('\n\n') if block.strip()]
                        json.dump({
                            'url': url,
                            'code_blocks': code_blocks,
                            'total_blocks': len(code_blocks)
                        }, temp_file)
                        temp_file.write('\n')
                    temp_file.flush()
                    logger.info(f"Created JSONL file: {temp_file.name}")
                    return send_file(temp_file.name, as_attachment=True, download_name='code_blocks.jsonl')
                    
            elif output_format == 'md':
                with tempfile.NamedTemporaryFile(mode='w+', delete=False, suffix='.md') as temp_file:
                    for url, content in valid_results.items():
                        temp_file.write(f"# {url}\n\n")
                        temp_file.write(content)
                        temp_file.write("\n\n---\n\n")
                    temp_file.flush()
                    logger.info(f"Created MD file: {temp_file.name}")
                    return send_file(temp_file.name, as_attachment=True, download_name='scraped_content.md')
                    
            elif output_format == 'html':
                with tempfile.NamedTemporaryFile(mode='w+', delete=False, suffix='.html') as temp_file:
                    html_content = "<html><body>"
                    for url, content in valid_results.items():
                        html_content += f"<h1>{url}</h1>"
                        html_content += f"<p>{content}</p>"
                        html_content += "<hr>"
                    html_content += "</body></html>"
                    temp_file.write(html_content)
                    temp_file.flush()
                    logger.info(f"Created HTML file: {temp_file.name}")
                    return send_file(temp_file.name, as_attachment=True, download_name='scraped_content.html')
                    
        except Exception as e:
            logger.error(f"Error creating {output_format} file: {str(e)}")
            return f"Error creating file: {str(e)}", 500
    
    return render_template('index.html')

@app.before_request
def log_request_info():
    logger.debug('Headers: %s', request.headers)
    logger.debug('Body: %s', request.get_data())

@app.route('/api/sync/config', methods=['POST'])
def create_sync_config():
    try:
        logger.debug('Received sync config request')
        if not request.is_json:
            return jsonify({'error': 'Content-Type must be application/json'}), 415
            
        data = request.get_json()
        logger.debug('Request data: %s', data)
        
        required_fields = ['doc_id', 'base_page_name', 'sync_interval']
        missing_fields = [field for field in required_fields if field not in data]
        if missing_fields:
            return jsonify({'error': f'Missing required fields: {", ".join(missing_fields)}'}), 400
            
        config_id = sync_manager.create_sync_config(
            doc_id=data['doc_id'],
            base_page_name=data['base_page_name'],
            sync_interval=data['sync_interval'],
            content_selector=data.get('content_selector'),
            code_selector=data.get('code_selector'),
            exclude_selector=data.get('exclude_selector')
        )
        
        if 'urls' in data:
            sync_manager.add_urls_to_config(config_id, data['urls'])
            
        return jsonify({'config_id': config_id, 'status': 'success'})
    except Exception as e:
        logger.error('Error in create_sync_config: %s', str(e))
        return jsonify({'error': str(e)}), 400

@app.route('/api/sync/config/<int:config_id>', methods=['GET'])
def get_sync_status(config_id):
    try:
        with sync_manager._get_db_connection() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
                # Get sync configuration
                cur.execute("""
                    SELECT * FROM sync_configurations WHERE id = %s
                """, (config_id,))
                config = cur.fetchone()
                if not config:
                    return jsonify({'error': 'Configuration not found'}), 404
                
                # Get latest sync history
                cur.execute("""
                    SELECT * FROM sync_history 
                    WHERE config_id = %s 
                    ORDER BY timestamp DESC 
                    LIMIT 1
                """, (config_id,))
                sync_history = cur.fetchone()
                
                # Get tracked URLs
                cur.execute("""
                    SELECT url, last_sync, last_content_hash 
                    FROM tracked_urls 
                    WHERE config_id = %s
                """, (config_id,))
                tracked_urls = cur.fetchall()
                
                return jsonify({
                    'config': dict(config) if config else None,
                    'last_sync': dict(sync_history) if sync_history else None,
                    'tracked_urls': [dict(url) for url in tracked_urls] if tracked_urls else []
                })
    except Exception as e:
        logger.error('Error in get_sync_status: %s', str(e))
        return jsonify({'error': str(e)}), 400

@app.route('/api/sync/config/<int:config_id>/urls', methods=['POST'])
def add_urls_to_config(config_id):
    try:
        if not request.is_json:
            return jsonify({'error': 'Content-Type must be application/json'}), 415
            
        data = request.get_json()
        if 'urls' not in data or not isinstance(data['urls'], list):
            return jsonify({'error': 'Request must include a "urls" array'}), 400
            
        sync_manager.add_urls_to_config(config_id, data['urls'])
        return jsonify({'status': 'success'})
    except Exception as e:
        logger.error('Error in add_urls_to_config: %s', str(e))
        return jsonify({'error': str(e)}), 400

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)