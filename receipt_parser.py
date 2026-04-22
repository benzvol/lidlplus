from bs4 import BeautifulSoup
import hashlib

def parse_number(value: str) -> float:
    """Parses a number that might use ',' or '.' as a decimal separator."""
    if value is None:
        return None
    value = value.replace(',', '.')  # Normalize decimal separator
    try:
        return float(value)
    except ValueError:
        return None

def hash_article(article_data: dict) -> str:
    """Creates a hash of the article's properties to detect duplicates."""
    article_string = f"{article_data['id']}{article_data['description']}{article_data['unit_price']}{article_data.get('quantity')}{article_data['tax_type']}"
    return hashlib.md5(article_string.encode()).hexdigest()

def parse_receipt(html: str) -> list:
    """Parses the receipt HTML and extracts articles, avoiding duplicates."""
    soup = BeautifulSoup(html, 'html.parser')
    articles = []
    previous_hash = None

    for span in soup.find_all('span', class_='article'):
        article_data = {
            'id': span.get('data-art-id', '').strip(),
            'description': span.get('data-art-description', '').strip(),
            'unit_price': parse_number(span.get('data-unit-price', '0').strip()),
            'quantity': parse_number(span.get('data-art-quantity')) if span.has_attr('data-art-quantity') else None,
            'tax_type': span.get('data-tax-type', '').strip()
        }
        
        article_hash = hash_article(article_data)
        if article_hash == previous_hash:
            continue
        previous_hash = article_hash
        
        articles.append(article_data)
    
    return articles
