import aiohttp
import asyncio
from bs4 import BeautifulSoup
from urllib.parse import urlparse
import trafilatura
import logging
from typing import Dict, List, Optional
import re

# Configure logging with more detailed format
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

MIN_CONTENT_LENGTH = 10  # Minimum number of characters for valid content

async def fetch_url_content(
    session: aiohttp.ClientSession, 
    url: str, 
    content_selector: Optional[str] = None,
    code_selector: Optional[str] = None,
    exclude_selector: Optional[str] = None
) -> str:
    try:
        # Validate URL
        parsed_url = urlparse(url)
        if not parsed_url.scheme or not parsed_url.netloc:
            logger.warning(f"Invalid URL: {url}")
            return "Error: Invalid URL"
        
        # Add user agent and handle redirects
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
        }
        
        # First try with trafilatura if no custom selectors are provided
        if not any([content_selector, code_selector, exclude_selector]):
            logger.info(f"Attempting to fetch content from {url} using trafilatura")
            downloaded = await asyncio.to_thread(trafilatura.fetch_url, url)
            
            if downloaded is not None:
                text_content = await asyncio.to_thread(trafilatura.extract, downloaded)
                if text_content:
                    logger.debug(f"Raw content extracted from {url}: {text_content[:200]}...")
                    return text_content.strip()
        
        # Fallback to aiohttp and BeautifulSoup with custom selectors
        logger.info(f"Fetching content from {url} using custom selectors")
        timeout = aiohttp.ClientTimeout(total=30)
        async with session.get(url, headers=headers, allow_redirects=True, timeout=timeout) as response:
            # Handle different response codes
            if response.status in [403, 401]:
                return f"Error: Access forbidden (HTTP {response.status}). Try a publicly accessible URL."
            elif response.status != 200:
                return f"Error: HTTP {response.status}"
                
            content = await response.text()
            soup = BeautifulSoup(content, 'html.parser')
            
            # Remove excluded content if specified
            if exclude_selector:
                for element in soup.select(exclude_selector):
                    element.decompose()
            
            text_content = []
            
            # Extract content based on custom selector
            if content_selector:
                content_elements = soup.select(content_selector)
                for element in content_elements:
                    text = element.get_text(strip=True)
                    if text:
                        text_content.append(text)
            
            # Extract code blocks with custom selector
            code_blocks = []
            if code_selector:
                code_elements = soup.select(code_selector)
            else:
                code_elements = soup.find_all(['code', 'pre', 'div.highlight', 'div.code'])
                
            for block in code_elements:
                code_text = block.get_text(strip=True)
                if code_text:
                    # Remove common noise
                    code_text = code_text.replace('Copy code', '').strip()
                    if len(code_text) > 5:  # Only add if has meaningful content
                        code_blocks.append(code_text)

            # If no content found with custom selectors, try default approach
            if not text_content and not code_blocks:
                # Look for elements with code-related classes
                code_elements = soup.find_all(class_=re.compile(r'code|highlight'))
                for element in code_elements:
                    code_text = element.get_text(strip=True)
                    if code_text and len(code_text) > 5:
                        code_blocks.append(code_text)
                
                # Get main content if no specific content found
                if not text_content:
                    text_content = [' '.join(soup.stripped_strings)]
            
            # Combine all content
            final_content = []
            if text_content:
                final_content.extend(text_content)
            if code_blocks:
                final_content.extend(code_blocks)
            
            text_content = '\n\n'.join(final_content)
            
            # Validate content
            if text_content is None or not isinstance(text_content, str):
                logger.warning(f"Invalid content type from {url}: {type(text_content)}")
                return "Error: Invalid content type"
                
            text_content = text_content.strip()
            
            if len(text_content) < MIN_CONTENT_LENGTH:
                logger.warning(f"Content too short from {url}: {len(text_content)} chars")
                return f"Error: Content too short (minimum {MIN_CONTENT_LENGTH} characters required)"
                
            logger.info(f"Successfully extracted content from {url} ({len(text_content)} chars)")
            return text_content
            
    except aiohttp.ClientError as e:
        logger.error(f"Request error for {url}: {str(e)}")
        return f"Error: {str(e)}"
    except Exception as e:
        logger.error(f"Unexpected error for {url}: {str(e)}")
        return f"Error: An unexpected error occurred - {str(e)}"

async def scrape_urls_async(
    urls: List[str],
    content_selector: Optional[str] = None,
    code_selector: Optional[str] = None,
    exclude_selector: Optional[str] = None
) -> Dict[str, str]:
    async with aiohttp.ClientSession() as session:
        tasks = [
            fetch_url_content(
                session, 
                url, 
                content_selector, 
                code_selector, 
                exclude_selector
            ) for url in urls
        ]
        results = await asyncio.gather(*tasks)
        return dict(zip(urls, results))

def scrape_urls(
    urls: List[str],
    content_selector: Optional[str] = None,
    code_selector: Optional[str] = None,
    exclude_selector: Optional[str] = None
) -> Dict[str, str]:
    return asyncio.run(scrape_urls_async(
        urls, 
        content_selector, 
        code_selector, 
        exclude_selector
    ))

async def test_scraper_async(url: str) -> Dict[str, str]:
    logger.info(f"Testing scraper with URL: {url}")
    result = await scrape_urls_async([url])
    content = list(result.values())[0]
    if content and not content.startswith('Error:'):
        logger.debug(f"Test result content preview: {content[:200]}...")
    return result

def test_scraper(url: str) -> Dict[str, str]:
    return asyncio.run(test_scraper_async(url))

if __name__ == "__main__":
    test_url = "https://www.example.com"
    test_result = test_scraper(test_url)
    print(f"Test result for {test_url}:")
    print(test_result[test_url][:500] + "..." if len(test_result[test_url]) > 500 else test_result[test_url])
