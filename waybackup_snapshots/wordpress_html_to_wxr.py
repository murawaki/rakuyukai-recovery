import os
import re
import requests
import warnings
import chardet
from datetime import datetime
import mimetypes
import logging
from bs4 import BeautifulSoup
from bs4 import XMLParsedAsHTMLWarning
from datetime import datetime
import dateutil.parser as dparser
import hashlib
from urllib.parse import quote, unquote, urlparse
import shutil

warnings.filterwarnings("ignore", category=XMLParsedAsHTMLWarning)

ROOT_DIR = 'www.rakuyukai.org'
MEDIA_DIR = 'media_files'
MEDIA_BASE_URL = 'https://www.rakuyukai.org/old_uploads'
SITE_BASE_URL = 'https://www.rakuyukai.org/raku'
DATE_FORMAT = '%Y-%m-%d %H:%M:%S'
LOG_FILE = 'script.log'
SKIPPED_FILE_LOG = 'skipped_files.log'

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s %(levelname)s: %(message)s',
    handlers=[
        logging.FileHandler(LOG_FILE, encoding='utf-8'),
        logging.StreamHandler()
    ]
)

os.makedirs(MEDIA_DIR, exist_ok=True)

JP_DATE_PATTERNS = [
    r'(\d{4})年(\d{1,2})月(\d{1,2})日',
    r'(\d{4})/(\d{1,2})/(\d{1,2})',
    r'(\d{4})-(\d{1,2})-(\d{1,2})',
]
    
def read_file_with_fallback(filepath):
    with open(filepath, 'rb') as f:
        raw_data = f.read()
    result = chardet.detect(raw_data)
    encoding = result['encoding'] or 'utf-8'
    try:
        return raw_data.decode(encoding)
    except UnicodeDecodeError:
        logging.warning(f"Skipping file due to decode error: {filepath}")
        with open(SKIPPED_FILE_LOG, 'a', encoding='utf-8') as logf:
            logf.write(f"Decode error: {filepath}\n")
        return None

def remove_global_noise(soup):
    for tag in soup(['nav', 'script', 'style']):
        tag.decompose()
    for ad_class in ['advertisement', 'ads', 'sponsored']:
        for ad in soup.find_all(class_=re.compile(ad_class, re.IGNORECASE)):
            ad.decompose()
    for sidebar in soup.find_all(class_=re.compile('sidebar', re.IGNORECASE)):
        sidebar.decompose()
    return soup

def parse_japanese_date(text):
    for pattern in JP_DATE_PATTERNS:
        match = re.search(pattern, text)
        if match:
            year, month, day = match.groups()
            try:
                date_str = f"{year}-{month.zfill(2)}-{day.zfill(2)}"
                return datetime.strptime(date_str, '%Y-%m-%d').strftime(DATE_FORMAT)
            except ValueError:
                continue
    return None

def extract_wayback_timestamp(filepath):
    match = re.search(r'/\d{14}/', filepath)
    if match:
        try:
            ts = match.group(0).strip('/')
            return datetime.strptime(ts, '%Y%m%d%H%M%S').strftime(DATE_FORMAT)
        except ValueError:
            return None
    return None

def find_local_media(src_url, canonical_src_url, subdir):
    m = re.search(r'/wp-content/uploads/(.+)', src_url)
    if not m:
        return None, None
    relative_path = m.group(1)

    m = re.search(r'/wp-content/uploads/(.+)', canonical_src_url)
    if not m:
        return None, None
    canonical_relative_path = m.group(1)

    dirname = os.path.dirname(relative_path)
    filename = os.path.basename(relative_path)
    canonical_filename = os.path.basename(canonical_relative_path)
    quoted_filename = quote(filename)
    search_dir = os.path.join(ROOT_DIR, subdir, 'wp-content', 'uploads', dirname)
    if not os.path.exists(search_dir):
        return None, None
    
    target_file = os.path.join(search_dir, quoted_filename)
    if os.path.exists(target_file):
        return target_file, os.path.join(subdir, dirname, canonical_filename) # not quoted_filename

    base_name = re.sub(r'-\d+x\d+(?=\.[a-z]+$)', '', quoted_filename)
    for file in os.listdir(search_dir):
        if file.startswith(base_name):
            logging.warning(f"Using resized_image: {file} for {src_url}")
            return os.path.join(search_dir, file), os.path.join(subdir, dirname, canonical_filename)

    return None, None

post_id_counter = 9000  # start after your blog post IDs
def next_post_id():
    global post_id_counter
    post_id_counter += 1
    return post_id_counter

def extract_content(article_tag, soup, filepath, site_url, subdir):
    article_classes = article_tag.get('class', [])
    if 'type-page' in article_classes:
        post_type = 'page'
    elif 'type-post' in article_classes:
        post_type = 'post'
    else:
        logging.warning(f"Using filepath to determine post type: {filepath}")
        if re.search(r'/20\d{2}/', filepath):
            post_type = 'post'
        elif '%' in filepath:
            post_type = 'page'
        else:
            post_type = 'post'

    title = None
    header_tag = article_tag.find('header')
    h1 = header_tag.find('h1', class_=re.compile(r'entry-title', re.I)) if header_tag else None
    if h1:
        title = h1.get_text().strip()
    elif soup.title and soup.title.string:
        title = soup.title.string.strip()
        if '|' in title:
            title = title.split('|')[0].strip()
        logging.warning(f"Substituting article title with page title: {filepath}")

    content_container = article_tag.find('div', class_=re.compile(r'entry-content', re.I))
    if not content_container:
        logging.warning(f"Skipping non-WordPress page (no content container): {filepath}")
        with open(SKIPPED_FILE_LOG, 'a', encoding='utf-8') as logf:
            logf.write(f"No content container: {filepath}\n")
        return None
    for tag in content_container(['nav', 'footer', 'aside', 'script', 'style']):
        tag.decompose()
    for tag in content_container.find_all(class_=re.compile(r'(sidebar|widget|footer|header)', re.I)):
        tag.decompose()

    date = None
    meta_date = soup.find('meta', {'property': 'article:published_time'})
    if meta_date and meta_date.get('content'):
        date = meta_date['content']

    if not date:
        time_tag = soup.find('time', class_=re.compile(r'entry-date'))
        if time_tag and time_tag.has_attr('datetime'):
            try:
                dt = dparser.parse(time_tag['datetime'])
                date = dt.strftime(DATE_FORMAT)
            except ValueError:
                pass

    if not date:
        footer_tag = soup.find('footer')
        if footer_tag:
            jp_date = parse_japanese_date(footer_tag.get_text())
            if jp_date:
                date = jp_date
                logging.warning(f"Using Japanese date from footer: {filepath}")

    if not date:
        date = extract_wayback_timestamp(filepath)
        if date:
            logging.warning(f"Using Wayback timestamp for date: {filepath}")

    if not date:
        m = re.search(r'/([12]\d{3})/(\d{1,2})/(\d{1,2})/', filepath)
        if m:
            year, month, day = m.groups()
            try:
                date = f"{year}-{month.zfill(2)}-{day.zfill(2)} 00:00:00"
                logging.warning(f"Using date from filepath pattern: {filepath}")
            except ValueError:
                pass

    if not date:
        m = re.search(r'/([12]\d{3})/(\d{1,2})/', filepath)
        if m:
            year, month = m.groups()
            try:
                date = f"{year}-{month.zfill(2)}-01 00:00:00"
                logging.warning(f"Using year/month from filepath pattern: {filepath}")
            except ValueError:
                pass

    if not date:
        m = re.search(r'/([12]\d{3})/', filepath)
        if m:
            year = m.group(1)
            try:
                date = f"{year}-01-01 00:00:00"
                logging.warning(f"Using year from filepath pattern: {filepath}")
            except ValueError:
                pass

    if not date:
        date = datetime.now().strftime(DATE_FORMAT)
        logging.warning(f"Using current time for date: {filepath}")

    content_hash = hashlib.md5(str(content_container).encode('utf-8')).hexdigest()
    post_id = int(content_hash[:6], 16)

    categories = []
    tags = []
    media_links = []
    media_entries = []
    for footer in article_tag.find_all('footer'):
        for cat in footer.find_all('a', href=re.compile(r'/category/')):
            categories.append(cat.get_text())
        for tag in footer.find_all('a', href=re.compile(r'/tag/')):
            tags.append(tag.get_text())

    for media in content_container.find_all(['img', 'video']):
        src = media.get('src')
        if not src or 'wp-content/uploads' not in src:
            logging.warning(f"Media file not found for src: {src} in {filepath}")
            continue

        canonical_src = src
        parent = media.find_parent('a')
        if parent and parent.has_attr('href'):
            href = parent['href']
            if href and 'wp-content/uploads' in href:
                canonical_src = href

        local_path, relative_dest = find_local_media(src, canonical_src, subdir)
        if local_path:
            media_url = f"{MEDIA_BASE_URL}/{relative_dest}"
            media_links.append(media_url)
            # copy file
            dest_path = os.path.join(MEDIA_DIR, relative_dest)
            os.makedirs(os.path.dirname(dest_path), exist_ok=True)
            shutil.copy2(local_path, dest_path)

            # build metadata for WXR
            # mtime = os.path.getmtime(local_path)
            # media_post_date = datetime.fromtimestamp(mtime).strftime("%Y-%m-%d %H:%M:%S")
            media_filename = os.path.basename(dest_path)
            media_title = os.path.splitext(unquote(media_filename))[0]
            media_entries.append({
                "post_id": next_post_id(),  # implement as a global counter or use uuid
                "post_date": date, # copy the date of the blog entry
                "title": media_title,
                "url": media_url,
                "filename": media_filename,
                "post_parent": post_id, # ID of the blog entry
            })
           

    if not title:
        h1 = content_container.find('h1', class_=re.compile(r'entry-title', re.I))
        if not h1:
            h1 = content_container.find(['h1', 'h2'])
        title = h1.get_text().strip() if h1 else "Untitled"

    if post_type == 'page':
        dir_name = os.path.basename(os.path.dirname(filepath))
        try:
            decoded_slug = requests.utils.unquote(dir_name)
        except Exception:
            decoded_slug = dir_name
        if not title or title == "Untitled":
            logging.warning(f"Using decoded directory name as title: {filepath}")
            title = decoded_slug
        guid = f"{site_url}/{decoded_slug}"
    else:
        guid = f"{site_url}/{post_id}"
    return title, date, str(content_container), categories, tags, media_links, media_entries, post_id, guid, post_type

def generate_wxr(posts, site_url):
    header = f"""<?xml version="1.0" encoding="UTF-8" ?>
<rss version="2.0"
    xmlns:excerpt="http://wordpress.org/export/1.2/excerpt/"
    xmlns:content="http://purl.org/rss/1.0/modules/content/"
    xmlns:wfw="http://wellformedweb.org/CommentAPI/"
    xmlns:dc="http://purl.org/dc/elements/1.1/"
    xmlns:wp="http://wordpress.org/export/1.2/">
<channel>
    <title>Recovered WordPress Site</title>
    <link>{site_url}</link>
    <description>Recovered from Wayback Machine</description>
    <wp:wxr_version>1.2</wp:wxr_version>
    <wp:base_site_url>{site_url}</wp:base_site_url>
    <wp:base_blog_url>{site_url}</wp:base_blog_url>
"""
    items = ""
    for post in posts:
        title, date, content, categories, tags, media_links, media_entries, post_id, guid, post_type = post
        item = f"""    <item>
        <title>{title}</title>
        <link>{guid}</link>
        <pubDate>{date}</pubDate>
        <dc:creator><![CDATA[admin]]></dc:creator>
        <guid isPermaLink=\"false\">{guid}</guid>
        <wp:post_id>{post_id}</wp:post_id>
        <content:encoded><![CDATA[{content}]]></content:encoded>
        <wp:post_date><![CDATA[{date}]]></wp:post_date>
        <wp:post_date_gmt><![CDATA[{date}]]></wp:post_date_gmt>
        <wp:post_type><![CDATA[{post_type}]]></wp:post_type>
        <wp:status><![CDATA[publish]]></wp:status>
"""
        for cat in categories:
            slug = {
                "お知らせ": "announcements",
                "資料庫": "materials",
                "トピックス": "topics",
                "未分類": "uncategorized",
                "活動実績": "achievements",
                "中国支部総会": "annual-meetings",
                "行事予定・報告": "events",
            }[cat]
            # slug = re.sub(r'[^a-zA-Z0-9]+', '-', cat.lower()).strip('-')
            item += f"        <category domain=\"category\" nicename=\"{slug}\"><![CDATA[{cat}]]></category>\n"
        for tag in tags:
            item += f"        <category domain=\"post_tag\"><![CDATA[{tag}]]></category>\n"
        for media in media_links:
            item += f"        <wp:attachment_url>{media}</wp:attachment_url>\n"
        item += "    </item>\n"
        items += item

        for media in media_entries:
            items += f"""  <item>
            <title>{media["title"]}</title>
            <link>{media["url"]}</link>
            <guid isPermaLink="false">{media["url"]}</guid>
            <description></description>
            <content:encoded><![CDATA[]]></content:encoded>
            <wp:post_id>{media["post_id"]}</wp:post_id>
            <wp:post_date>{media["post_date"]}</wp:post_date>
            <wp:post_date_gmt>{media["post_date"]}</wp:post_date_gmt>
            <wp:comment_status>closed</wp:comment_status>
            <wp:ping_status>closed</wp:ping_status>
            <wp:post_name>{media["title"]}</wp:post_name>
            <wp:status>inherit</wp:status>
            <wp:post_parent>{media["post_parent"] or 0}</wp:post_parent>
            <wp:menu_order>0</wp:menu_order>
            <wp:post_type>attachment</wp:post_type>
            <wp:post_password></wp:post_password>
            <wp:is_sticky>0</wp:is_sticky>
            <wp:attachment_url>{media["url"]}</wp:attachment_url>
            </item>
"""
    footer = "</channel>\n</rss>\n"
    return header + items + footer

seen_hashes = set()
def process_site(subdir, exclude=set()):
    site_path = os.path.join(ROOT_DIR, subdir)
    site_url = f"{SITE_BASE_URL.rstrip('/')}/{subdir.strip('/')}".rstrip('/')
    logging.info(f"=== Processing site: {site_url} ===")
    posts = []
    total = 0
    for root, dirs, files in os.walk(site_path, topdown=False):
        if any(os.path.commonpath([root]).startswith(os.path.join(site_path, d)) for d in exclude):
            continue
        if subdir == '' and os.path.exists(os.path.join(root, 'wp-login.php')):
            continue
        if subdir == '':
                dirs[:] = [d for d in dirs if not os.path.exists(os.path.join(ROOT_DIR, d, 'wp-login.php'))]
        for filename in files:
            if filename.endswith('.html'):
                total += 1
                filepath = os.path.join(root, filename)
                logging.info(f"Processing: {filepath}")
                html = read_file_with_fallback(filepath)
                if html is None:
                    continue
                soup = BeautifulSoup(html, 'html.parser')
                soup = remove_global_noise(soup)
                article_blocks = soup.find_all('article')
                if not article_blocks:
                    logging.warning(f"Skipping non-WordPress page (no <article>): {filepath}")
                    with open(SKIPPED_FILE_LOG, 'a', encoding='utf-8') as logf:
                        logf.write(f"No <article>: {filepath}")
                    continue
                for article_tag in article_blocks:
                    extracted = extract_content(article_tag, soup, filepath, site_url, subdir)
                    if extracted:
                        content_hash = hashlib.md5(str(extracted[2]).encode('utf-8')).hexdigest()
                        if content_hash in seen_hashes:
                            logging.info(f"Duplicate post skipped: {filepath}")
                            continue
                        seen_hashes.add(content_hash)
                        posts.append(extracted)
    logging.info(f"Processed {len(posts)} out of {total} HTML files.")
    if posts:
        output_file = f"wordpress_export_{subdir.strip('/').replace('/', '_')}.xml"
        wxr_data = generate_wxr(posts, site_url)
        with open(output_file, 'w', encoding='utf-8') as f:
            f.write(wxr_data)
        logging.info(f"WXR file generated: {output_file}")
    else:
        logging.info(f"No posts found in: {site_url}, skipping WXR generation.")

def main():
    def is_wordpress_site(path):
        return os.path.exists(os.path.join(ROOT_DIR, path, 'wp-login.php'))

    subdirs = ['']
    subdirs += [entry for entry in os.listdir(ROOT_DIR)
                if os.path.isdir(os.path.join(ROOT_DIR, entry)) and is_wordpress_site(entry)]

    for subdir in subdirs:
        if subdir == '':
            exclude = set(subdirs[1:])
            process_site(subdir, exclude)
        else:
            process_site(subdir)

if __name__ == "__main__":
    main()

