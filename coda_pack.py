from codaio import Coda
import os
from typing import List, Dict, Optional, Union
import requests
from pydantic import BaseModel, HttpUrl, validator
import re
from urllib.parse import urljoin
import json
import trafilatura
from bs4 import BeautifulSoup
from utils.template_manager import TemplateManager

# Initialize Coda client with API key
coda = Coda(os.environ["CODA_API_KEY"])

class CSSSelector(BaseModel):
    selector: str
    
    @validator('selector')
    def validate_css_selector(cls, v):
        if not v:
            return v
        valid_patterns = [
            r'^[a-zA-Z0-9#\.\-_\[\]="\'~^$*|]+$',  # Basic selectors
            r'^[a-zA-Z0-9#\.\-_]+(?:\s+[a-zA-Z0-9#\.\-_]+)*$',  # Descendant selectors
            r'^[a-zA-Z0-9#\.\-_]+(?:\s*>[a-zA-Z0-9#\.\-_]+)*$',  # Child selectors
        ]
        if not any(re.match(pattern, v) for pattern in valid_patterns):
            raise ValueError(f"Invalid CSS selector: {v}")
        return v

class URLInput(BaseModel):
    urls: List[HttpUrl]
    content_selector: Optional[Union[str, List[str]]] = None
    code_selector: Optional[str] = None
    exclude_selector: Optional[str] = None
    output_format: str = "json"
    template_type: Optional[str] = None
    
    @validator('content_selector')
    def validate_content_selectors(cls, v):
        if v is None:
            return v
        if isinstance(v, str):
            v = [v]
        for selector in v:
            CSSSelector(selector=selector)
        return v

def extract_content(urls: List[str], content_selector: Optional[Union[str, List[str]]] = None, 
                   code_selector: Optional[str] = None, 
                   exclude_selector: Optional[str] = None) -> Dict[str, str]:
    results = {}
    
    if isinstance(content_selector, str):
        content_selector = [content_selector] if content_selector else []
    elif content_selector is None:
        content_selector = []
        
    for url in urls:
        try:
            for selector in content_selector:
                CSSSelector(selector=selector)
            if code_selector:
                CSSSelector(selector=code_selector)
            if exclude_selector:
                CSSSelector(selector=exclude_selector)
                
            downloaded = trafilatura.fetch_url(url)
            if downloaded:
                if content_selector or code_selector or exclude_selector:
                    soup = BeautifulSoup(downloaded, 'html.parser')
                    
                    content = []
                    
                    for selector in content_selector:
                        try:
                            elements = soup.select(selector)
                            content.extend([elem.get_text(separator=' ', strip=True) 
                                         for elem in elements if elem.get_text(strip=True)])
                        except Exception as selector_error:
                            content.append(f"Error with selector '{selector}': {str(selector_error)}")
                    
                    if code_selector:
                        try:
                            code_elements = soup.select(code_selector)
                            content.extend([elem.get_text(separator='\n', strip=True) 
                                         for elem in code_elements if elem.get_text(strip=True)])
                        except Exception as code_error:
                            content.append(f"Error with code selector: {str(code_error)}")
                    
                    if exclude_selector:
                        try:
                            for elem in soup.select(exclude_selector):
                                elem.decompose()
                        except Exception as exclude_error:
                            content.append(f"Error with exclude selector: {str(exclude_error)}")
                    
                    results[url] = "\n\n".join(content) if content else "No content found"
                else:
                    extracted_text = trafilatura.extract(downloaded)
                    results[url] = extracted_text if extracted_text else "No content found"
            else:
                results[url] = "Error: Failed to download content"
        except Exception as e:
            results[url] = f"Error: {str(e)}"
    
    return results

def Formula_ExtractContent(urls: List[str], content_selector: Union[str, List[str]] = "", 
                         code_selector: str = "", 
                         exclude_selector: str = "") -> Dict[str, str]:
    return extract_content(urls, content_selector, code_selector, exclude_selector)

def Formula_PreviewContent(urls: List[str], content_selector: Union[str, List[str]] = "", 
                         code_selector: str = "", 
                         exclude_selector: str = "", 
                         max_preview_length: int = 500) -> Dict[str, Dict[str, str]]:
    results = extract_content(urls, content_selector, code_selector, exclude_selector)
    previews = {}
    
    for url, content in results.items():
        if content.startswith("Error:"):
            preview_data = {
                "status": "error",
                "error_message": content,
                "preview": "",
                "content_length": 0
            }
        else:
            content_length = len(content)
            preview = content[:max_preview_length] + ("..." if content_length > max_preview_length else "")
            preview_data = {
                "status": "success",
                "preview": preview,
                "content_length": content_length,
                "has_more": content_length > max_preview_length
            }
        previews[url] = preview_data
    
    return previews

def Formula_GenerateSubPage(doc_id: str, page_name: str, 
                          content: Dict[str, str],
                          template_type: Optional[str] = None) -> str:
    try:
        page_content = ""
        
        for url, text in content.items():
            if not text.startswith("Error:"):
                # Detect content type if not specified
                detected_type = template_type or TemplateManager.detect_content_type(text)
                # Format content using template
                formatted_content = TemplateManager.format_template(
                    template_type=detected_type,
                    content=text,
                    url=url
                )
                page_content += formatted_content + "\n\n---\n\n"
        
        try:
            doc = coda.get_doc(doc_id)
            payload = {
                "name": page_name,
                "subtitle": "Extracted URL Content",
                "body": page_content
            }
            
            headers = {
                "Authorization": f"Bearer {os.environ['CODA_API_KEY']}",
                "Content-Type": "application/json"
            }
            response = requests.post(
                f"https://coda.io/apis/v1/docs/{doc_id}/pages",
                headers=headers,
                json=payload
            )
            
            if response.status_code == 200:
                data = response.json()
                return f"https://coda.io/d/{doc_id}/p/{data['id']}"
            else:
                return f"Error: Failed to create page (Status: {response.status_code})"
                
        except Exception as inner_e:
            return f"Error creating page: {str(inner_e)}"
            
    except Exception as e:
        return f"Error: {str(e)}"

def Formula_BatchExtractAndGenerate(doc_id: str, urls: List[str], 
                                  base_page_name: str = "URL Content",
                                  content_selector: Union[str, List[str]] = "",
                                  code_selector: str = "",
                                  exclude_selector: str = "",
                                  template_type: Optional[str] = None) -> List[str]:
    try:
        all_content = extract_content(urls, content_selector, code_selector, exclude_selector)
        
        page_urls = []
        batch_size = 5
        for i in range(0, len(urls), batch_size):
            batch_urls = urls[i:i+batch_size]
            batch_content = {url: all_content[url] for url in batch_urls if url in all_content}
            
            if batch_content:
                page_name = f"{base_page_name} - Batch {i//batch_size + 1}"
                page_url = Formula_GenerateSubPage(doc_id, page_name, batch_content, template_type)
                page_urls.append(page_url)
        
        return page_urls
    except Exception as e:
        return [f"Error processing batch: {str(e)}"]