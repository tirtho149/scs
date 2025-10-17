from scholarly import scholarly
import time
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
import random
import json
from datetime import datetime
import os

# ----------------------------
# CONFIGURATION
# ----------------------------
SCHOLAR_ID = "-rmRjqIAAAAJ"  # Soumik Sarkar's Google Scholar ID
CV_LAST_UPDATE_DATE = "2025-10-05"  # Date from your CV
OUTPUT_JSON = "publications.json"
OUTPUT_CV_FORMAT = "cv_formatted.txt"
MAX_WORKERS = 1  # Reduced to 1 for GitHub Actions
MIN_DELAY = 3.0  # Increased delays for GitHub Actions
MAX_DELAY = 5.0
MAX_RETRIES = 5  # More retries for GitHub Actions

# Keywords to identify conference vs journal papers
CONFERENCE_KEYWORDS = [
    'conference', 'proceedings', 'workshop', 'symposium', 'meeting',
    'nips', 'neurips', 'icml', 'cvpr', 'iccv', 'eccv', 'aaai', 'ijcai',
    'iclr', 'acm', 'ieee', 'iccps', 'acc', 'cdc', 'dscc', 'mecc',
    'allerton', 'siam', 'asilomar', 'hpec'
]

# Keywords to identify preprints
PREPRINT_KEYWORDS = ['arxiv', 'preprint', 'biorxiv', 'medrxiv', 'ssrn']

# ----------------------------
# SETUP PROXY
# ----------------------------
print("🔧 Setting up Tor SOCKS5 proxy...")
proxy_working = False

try:
    # Set SOCKS5 proxy via environment variables (most compatible method)
    os.environ['http_proxy'] = 'socks5h://127.0.0.1:9050'
    os.environ['https_proxy'] = 'socks5h://127.0.0.1:9050'
    os.environ['HTTP_PROXY'] = 'socks5h://127.0.0.1:9050'
    os.environ['HTTPS_PROXY'] = 'socks5h://127.0.0.1:9050'
    
    print("  ✅ Tor SOCKS5 proxy configured!")
    proxy_working = True
    
except Exception as e:
    print(f"  ❌ Proxy setup error: {e}")

if not proxy_working:
    print("\n❌ Could not configure proxy!")
    exit(1)

print()

# Test connection
print("🔍 Testing Tor connection...")
try:
    import requests
    response = requests.get('https://check.torproject.org/api/ip', 
                          proxies={
                              'http': 'socks5h://127.0.0.1:9050',
                              'https': 'socks5h://127.0.0.1:9050'
                          },
                          timeout=30)
    if response.json().get('IsTor'):
        print("  ✅ Connected through Tor network!")
    else:
        print("  ⚠️  Warning: Not connected through Tor")
except Exception as e:
    print(f"  ⚠️  Could not verify Tor connection: {e}")
    print("  Proceeding anyway...")

print()

# ----------------------------
# FETCH AUTHOR DATA WITH RETRIES
# ----------------------------
print("🔍 Fetching author profile from Google Scholar...")

author = None
for attempt in range(MAX_RETRIES):
    try:
        print(f"  Attempt {attempt + 1}/{MAX_RETRIES}...")
        
        # Add delay before each attempt
        if attempt > 0:
            wait_time = 2 ** attempt  # Exponential backoff
            print(f"  Waiting {wait_time} seconds...")
            time.sleep(wait_time)
        
        # Search for author
        search_query = scholarly.search_author_id(SCHOLAR_ID)
        
        # Check if we got a result
        if search_query is None:
            print(f"  ⚠️  Got None from search, retrying...")
            continue
            
        # Fill the author details
        author = scholarly.fill(search_query, sections=["publications"])
        
        # Verify we have publications
        if author and 'publications' in author:
            print(f"  ✅ Found {len(author['publications'])} publications\n")
            break
        else:
            print(f"  ⚠️  No publications found, retrying...")
            author = None
            
    except AttributeError as e:
        print(f"  ⚠️  AttributeError: {e}")
        if attempt == MAX_RETRIES - 1:
            print(f"\n❌ Failed after {MAX_RETRIES} attempts")
            print("Google Scholar might be blocking requests.")
            print("Try running this script locally with Tor, or wait and retry later.")
            exit(1)
    except Exception as e:
        print(f"  ⚠️  Error: {e}")
        if attempt == MAX_RETRIES - 1:
            print(f"\n❌ Failed after {MAX_RETRIES} attempts: {e}")
            exit(1)

if author is None:
    print("\n❌ Could not fetch author data after all retries")
    exit(1)

# Storage for categorized publications
journal_papers = []
conference_papers = []
preprints = []

# ----------------------------
# HELPER FUNCTIONS
# ----------------------------
def safe_str(value):
    """Convert any value to a safe string"""
    if value is None:
        return ""
    return str(value).strip()

def random_delay():
    """Random delay to avoid pattern detection"""
    time.sleep(random.uniform(MIN_DELAY, MAX_DELAY))

def is_preprint(venue):
    """Check if publication is a preprint"""
    venue_lower = venue.lower()
    return any(keyword in venue_lower for keyword in PREPRINT_KEYWORDS)

def is_conference(venue):
    """Check if publication is a conference paper"""
    venue_lower = venue.lower()
    return any(keyword in venue_lower for keyword in CONFERENCE_KEYWORDS)

def format_authors_initials(authors_str):
    """
    Convert authors string to 'Initial. Last' style separated by commas and 'and' before last author.
    Example: "Henry C Croll and Kaoru Ikuma" -> "H. C. Croll, and K. Ikuma"
    """
    if not authors_str:
        return ""
    
    # Split by 'and' to separate authors
    authors = [a.strip() for a in re.split(r'\s+and\s+', authors_str)]
    formatted = []
    
    for name in authors:
        parts = name.split()
        if len(parts) == 1:
            formatted.append(parts[0])
        else:
            # Get initials for all but last name
            initials = [p[0] + "." for p in parts[:-1]]
            formatted.append(" ".join(initials) + " " + parts[-1])
    
    # Join with commas and 'and' before last author
    if len(formatted) > 1:
        return ", ".join(formatted[:-1]) + ", and " + formatted[-1]
    return formatted[0]

def format_journal_entry_cv_style(pub_data):
    """
    Format journal paper in the exact desired style:
    Authors. "Title" **Journal** volume (year): pages.
    """
    authors = format_authors_initials(pub_data['authors'])
    title = pub_data['title']
    journal = pub_data['venue']
    year = pub_data['year']
    volume = pub_data.get('volume', '')
    pages = pub_data.get('pages', '')
    
    # Build citation
    entry = f'{authors}. "{title}" **{journal}**'
    
    if volume:
        entry += f' {volume}'
    
    if year:
        entry += f' ({year})'
    
    if pages:
        entry += f': {pages}'
    
    entry += '.'
    
    return entry

def format_conference_entry_cv_style(pub_data):
    """
    Format conference paper in the exact desired style:
    Authors. "Title" **Conference** (year).
    """
    authors = format_authors_initials(pub_data['authors'])
    title = pub_data['title']
    venue = pub_data['venue']
    year = pub_data['year']
    
    # Build citation
    entry = f'{authors}. "{title}" **{venue}**'
    
    if year:
        entry += f' ({year})'
    
    entry += '.'
    
    return entry

def check_if_in_cv(title, existing_cv_text):
    """
    Check if a publication title already exists in the CV
    """
    if not existing_cv_text:
        return False
        
    # Clean the title for comparison
    clean_title = re.sub(r'[^\w\s]', '', title.lower())
    clean_cv = re.sub(r'[^\w\s]', '', existing_cv_text.lower())
    
    # Check for substantial match (80% of words)
    title_words = set(clean_title.split())
    if len(title_words) == 0:
        return False
    
    matches = sum(1 for word in title_words if word in clean_cv and len(word) > 3)
    match_ratio = matches / len(title_words)
    
    return match_ratio > 0.8

def process_publication(idx, pub, total, existing_cv_text):
    """Process a single publication with retries"""
    for attempt in range(MAX_RETRIES):
        try:
            print(f"[{idx}/{total}] Fetching...", end="", flush=True)
            
            # Fetch full publication data
            full_pub = scholarly.fill(pub)
            bib_info = full_pub.get("bib", {})
            
            title = safe_str(bib_info.get("title"))
            venue = safe_str(bib_info.get("venue") or bib_info.get("journal") or bib_info.get("citation"))
            year = safe_str(bib_info.get("pub_year"))
            authors = safe_str(bib_info.get("author"))
            
            # Get additional info
            volume = safe_str(bib_info.get("volume"))
            pages = safe_str(bib_info.get("pages"))
            publisher = safe_str(bib_info.get("publisher"))
            
            if not title:
                print(f" ⚠️  No title, skipped")
                return None
            
            # Check if already in CV
            if check_if_in_cv(title, existing_cv_text):
                print(f" ⏭️  Already in CV: {title[:40]}...")
                return None
            
            # Categorize publication
            pub_data = {
                'title': title,
                'authors': authors,
                'venue': venue,
                'year': year,
                'volume': volume,
                'pages': pages,
                'publisher': publisher,
                'scholar_url': full_pub.get('pub_url', ''),
                'citations': full_pub.get('num_citations', 0)
            }
            
            if is_preprint(venue):
                pub_type = "PREPRINT"
                category = "preprint"
            elif is_conference(venue):
                pub_type = "CONFERENCE"
                category = "conference"
            else:
                pub_type = "JOURNAL"
                category = "journal"
            
            print(f" ✅ {year} - {title[:40]}... ({pub_type})")
            
            random_delay()
            return (pub_data, category)
            
        except Exception as e:
            if attempt < MAX_RETRIES - 1:
                print(f" ⚠️  Retry {attempt + 1}/{MAX_RETRIES}...", end="", flush=True)
                time.sleep(2 ** attempt)
            else:
                print(f" ❌ Failed after {MAX_RETRIES} attempts: {e}")
                return None

# ----------------------------
# LOAD EXISTING CV
# ----------------------------
print("📄 Loading existing CV to check for duplicates...")
existing_cv_text = ""
try:
    cv_filenames = ['cv_draft.txt', 'CV.txt', 'vita.txt', 'faculty_vita.txt']
    cv_loaded = False
    
    for filename in cv_filenames:
        try:
            with open(filename, 'r', encoding='utf-8') as f:
                existing_cv_text = f.read()
            print(f"✅ CV loaded from {filename}\n")
            cv_loaded = True
            break
        except FileNotFoundError:
            continue
    
    if not cv_loaded:
        print("⚠️  CV file not found. Will process all publications.\n")
        
except Exception as e:
    print(f"⚠️  Error loading CV: {e}")
    print("Will process all publications.\n")
    existing_cv_text = ""

# ----------------------------
# PROCESS PUBLICATIONS
# ----------------------------
total = len(author["publications"])
print("=" * 70)
print(f"🚀 Processing {total} publications...\n")

start_time = time.time()

# Process sequentially for GitHub Actions (more reliable)
for idx, pub in enumerate(author["publications"], 1):
    result = process_publication(idx, pub, total, existing_cv_text)
    if result:
        pub_data, category = result
        if category == "preprint":
            preprints.append(pub_data)
        elif category == "conference":
            conference_papers.append(pub_data)
        else:
            journal_papers.append(pub_data)

elapsed = time.time() - start_time
print("\n" + "=" * 70)

# ----------------------------
# SORT BY YEAR (NEWEST FIRST)
# ----------------------------
journal_papers.sort(key=lambda x: x.get('year', '0'), reverse=True)
conference_papers.sort(key=lambda x: x.get('year', '0'), reverse=True)
preprints.sort(key=lambda x: x.get('year', '0'), reverse=True)

# ----------------------------
# GENERATE CV-FORMATTED OUTPUT
# ----------------------------
print("\n💾 Generating CV-formatted additions...")

cv_output = []
cv_output.append("=" * 70)
cv_output.append("NEW PUBLICATIONS TO ADD TO CV")
cv_output.append("=" * 70)
cv_output.append(f"Generated on: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
cv_output.append(f"From Google Scholar ID: {SCHOLAR_ID}")
cv_output.append("")

# Journal Papers
if journal_papers:
    cv_output.append("\n" + "=" * 70)
    cv_output.append("JOURNAL PAPERS")
    cv_output.append("Add to Section: II.A.1 - Articles in Peer-Reviewed Journals")
    cv_output.append("=" * 70)
    cv_output.append("\nDuring ISU appointment\n")
    
    for pub in journal_papers:
        entry = format_journal_entry_cv_style(pub)
        cv_output.append(entry)
        cv_output.append("")

# Conference Papers
if conference_papers:
    cv_output.append("\n" + "=" * 70)
    cv_output.append("CONFERENCE PAPERS")
    cv_output.append("Add to Section: II.A.3 - Peer-Reviewed Conference Proceedings")
    cv_output.append("=" * 70)
    cv_output.append("\nDuring ISU appointment\n")
    
    for pub in conference_papers:
        entry = format_conference_entry_cv_style(pub)
        cv_output.append(entry)
        cv_output.append("")

# Preprints
if preprints:
    cv_output.append("\n" + "=" * 70)
    cv_output.append("PREPRINTS (ArXiv, etc.)")
    cv_output.append("=" * 70)
    cv_output.append("")
    
    for pub in preprints:
        entry = format_conference_entry_cv_style(pub)
        cv_output.append(entry)
        cv_output.append("")

# Add manual review notes
cv_output.append("\n" + "=" * 70)
cv_output.append("MANUAL REVIEW REQUIRED")
cv_output.append("=" * 70)
cv_output.append("")
cv_output.append("Before adding to your CV, please:")
cv_output.append("1. Mark graduate students with + after their names")
cv_output.append("2. Mark undergraduate students with * after their names")
cv_output.append("3. Verify all author names and initials")
cv_output.append("4. Check volume and page numbers for accuracy")
cv_output.append("5. Verify journal/conference names")
cv_output.append("6. Add numbering to each section")
cv_output.append("7. Update the CV date at the top of the document")
cv_output.append("8. Note: Journal/conference names are in **bold** (use Word bold formatting)")
cv_output.append("")

# Save CV-formatted output
with open(OUTPUT_CV_FORMAT, 'w', encoding='utf-8') as f:
    f.write('\n'.join(cv_output))

# ----------------------------
# SAVE JSON
# ----------------------------
json_output = {
    'generated_date': datetime.now().isoformat(),
    'cv_last_update': CV_LAST_UPDATE_DATE,
    'scholar_id': SCHOLAR_ID,
    'journal_papers': journal_papers,
    'conference_papers': conference_papers,
    'preprints': preprints,
    'statistics': {
        'total_found': len(author["publications"]),
        'new_journals': len(journal_papers),
        'new_conferences': len(conference_papers),
        'new_preprints': len(preprints),
        'processing_time_seconds': round(elapsed, 2)
    }
}

with open(OUTPUT_JSON, 'w', encoding='utf-8') as f:
    json.dump(json_output, f, indent=2, ensure_ascii=False)

# ----------------------------
# SUMMARY
# ----------------------------
print("\n✨ SYNC COMPLETE!\n")
print("📊 Summary:")
print(f"   ⏱️  Time: {elapsed:.1f}s")
print(f"   📚 New Journal Papers: {len(journal_papers)}")
print(f"   📄 New Conference Papers: {len(conference_papers)}")
print(f"   📝 New Preprints: {len(preprints)}")
print(f"   ✅ Total New Publications: {len(journal_papers) + len(conference_papers) + len(preprints)}")

print("\n📝 Output Files:")
print(f"   1. {OUTPUT_CV_FORMAT} - CV-formatted entries")
print(f"   2. {OUTPUT_JSON} - Detailed JSON")

print("\n" + "=" * 70)
print("✅ Done!")
print("=" * 70)
