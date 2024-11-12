from typing import Dict, Optional, List, Tuple
import json
from datetime import datetime
import re

class TemplateManager:
    DEFAULT_TEMPLATE = {
        "article": """# {title}

{toc}

{content}

---
Source: {url}
Extracted: {timestamp}
""",
        "code": """# {title}

{toc}

## Code Blocks

{content}

---
Source: {url}
Extracted: {timestamp}
""",
        "documentation": """# {title}

## Table of Contents
{toc}

## Content
{content}

---
Source: {url}
Last Updated: {timestamp}
""",
        "blog": """# {title}

{toc}

{content}

## Metadata
- Source: {url}
- Published: {timestamp}
- Reading Time: {reading_time} min
"""
    }

    @staticmethod
    def _generate_toc(content: str) -> Tuple[str, str]:
        """Generate table of contents and update content with anchor links"""
        toc_lines = []
        processed_content_lines = []
        current_heading_id = 0
        
        # Split content into lines and process each line
        lines = content.split('\n')
        heading_pattern = re.compile(r'^(#{1,6})\s+(.+)$')
        
        for line in lines:
            match = heading_pattern.match(line)
            if match:
                level = len(match.group(1))  # Number of # symbols
                heading_text = match.group(2).strip()
                
                # Create a unique ID for the heading
                heading_id = f"heading-{current_heading_id}"
                current_heading_id += 1
                
                # Add to TOC with proper indentation and link
                indent = "  " * (level - 1)
                toc_lines.append(f"{indent}- [{heading_text}](#{heading_id})")
                
                # Add anchor to the heading in content
                processed_content_lines.append(f'{match.group(1)} {heading_text} <a id="{heading_id}"></a>')
            else:
                processed_content_lines.append(line)
        
        toc = "## Table of Contents\n" + "\n".join(toc_lines) if toc_lines else ""
        processed_content = "\n".join(processed_content_lines)
        
        return toc, processed_content

    @staticmethod
    def get_template(template_type: str) -> str:
        """Get the template string for a specific content type"""
        return TemplateManager.DEFAULT_TEMPLATE.get(template_type, 
                                                   TemplateManager.DEFAULT_TEMPLATE['article'])

    @staticmethod
    def format_template(template_type: str, content: str, url: str, 
                       title: Optional[str] = None) -> str:
        """Format content using the specified template"""
        template = TemplateManager.get_template(template_type)
        
        # Prepare template variables
        title = title or url
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        reading_time = max(1, len(content.split()) // 200)  # Estimate reading time
        
        # Generate table of contents and update content with anchor links
        toc, processed_content = TemplateManager._generate_toc(content)
        
        # Format the template
        return template.format(
            title=title,
            content=processed_content,
            url=url,
            timestamp=timestamp,
            toc=toc,
            reading_time=reading_time
        )

    @staticmethod
    def detect_content_type(content: str) -> str:
        """Automatically detect the most appropriate template type based on content"""
        # Count code blocks
        code_block_indicators = ['```', 'def ', 'class ', 'function', 'return']
        code_score = sum(content.count(indicator) for indicator in code_block_indicators)
        
        # Check for documentation patterns
        doc_indicators = ['## ', '### ', 'Table of Contents', 'Installation', 'Usage']
        doc_score = sum(content.count(indicator) for indicator in doc_indicators)
        
        # Check for blog patterns
        blog_indicators = ['Posted on', 'Author:', 'Comments', 'Tags:']
        blog_score = sum(content.count(indicator) for indicator in blog_indicators)
        
        # Determine the best template type
        scores = {
            'code': code_score,
            'documentation': doc_score,
            'blog': blog_score
        }
        
        max_score_type = max(scores.items(), key=lambda x: x[1])[0]
        return max_score_type if scores[max_score_type] > 0 else 'article'
