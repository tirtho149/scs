from scholarly import scholarly
import time
import re
import random
import json
from datetime import datetime
import os

# ----------------------------
# CONFIGURATION
# ----------------------------
SCHOLAR_ID = "-rmRjqIAAAAJ"
CV_LAST_UPDATE_DATE = "2025-10-05"
OUTPUT_JSON = "publications.json"
OUTPUT_CV_FORMAT = "cv_formatted.txt"
CHECKPOINT_FILE = "checkpoint.json"   # ← NEW: saves progress
MAX_WORKERS = 1
MIN_DELAY = 8.0    # ← increased: more human-like
MAX_DELAY = 15.0   # ← increased
MAX_RETRIES = 5
CIRCUIT_RENEW_AFTER = 20  # ← NEW: renew Tor circuit every N publications

CONFERENCE_KEYWORDS = [
    'conference', 'proceedings', 'workshop', 'symposium', 'meeting',
    'nips', 'neurips', 'icml', 'cvpr', 'iccv', 'eccv', 'aaai', 'ijcai',
    'iclr', 'acm', 'ieee', 'iccps', 'acc', 'cdc', 'dscc', 'mecc',
    'allerton', 'siam', 'asilomar', 'hpec'
]
PREPRINT_KEYWORDS = ['arxiv', 'preprint', 'biorxiv', 'medrxiv', 'ssrn']

# ----------------------------
# SETUP TOR PROXY
# ----------------------------
print("🔧 Setting up Tor SOCKS5 proxy...")

os.environ['http_proxy'] = 'socks5h://127.0.0.1:9050'
os.environ['https_proxy'] = 'socks5h://127.0.0.1:9050'
os.environ['HTTP_PROXY'] = 'socks5h://127.0.0.1:9050'
os.environ['HTTPS_PROXY'] = 'socks5h://127.0.0.1:9050'

# ← NEW: Block scholarly from trying to use Firefox/geckodriver at all.
# scholarly falls back to Selenium when it detects a CAPTCHA, but in
# GitHub Actions geckodriver can't be downloaded.  Setting this env var
# tells webdriver-manager to look only locally (it will fail fast and
# cleanly instead of hanging for minutes on a network request).
os.environ['WDM_LOCAL'] = '1'

print("  ✅ Tor SOCKS5 proxy configured!\n")

# ----------------------------
# TOR CIRCUIT RENEWAL
# ----------------------------
def renew_tor_circuit():
    """Request a new Tor exit node via the control port."""
    try:
        from stem import Signal
        from stem.control import Controller
        with Controller.from_port(port=9051) as ctrl:
            ctrl.authenticate()
            ctrl.signal(Signal.NEWNYM)
        print("  🔄 Tor circuit renewed — new exit node assigned")
        time.sleep(6)   # Wait for circuit to build
        return True
    except Exception as e:
        print(f"  ⚠️  Circuit renewal failed (stem not available?): {e}")
        return False

# ----------------------------
# TEST TOR CONNECTION
# ----------------------------
print("🔍 Testing Tor connection...")
try:
    import requests
    response = requests.get(
        'https://check.torproject.org/api/ip',
        proxies={'http': 'socks5h://127.0.0.1:9050',
                 'https': 'socks5h://127.0.0.1:9050'},
        timeout=30
    )
    data = response.json()
    if data.get('IsTor'):
        print(f"  ✅ Connected through Tor! Exit IP: {data.get('IP', 'unknown')}\n")
    else:
        print(f"  ⚠️  Not through Tor (IP: {data.get('IP', 'unknown')}), proceeding anyway\n")
except Exception as e:
    print(f"  ⚠️  Tor check failed: {e}\n  Proceeding anyway...\n")

# ----------------------------
# CHECKPOINT HELPERS
# ----------------------------
def load_checkpoint():
    """Load previously saved progress."""
    if os.path.exists(CHECKPOINT_FILE):
        try:
            with open(CHECKPOINT_FILE, 'r') as f:
                data = json.load(f)
            print(f"📂 Checkpoint found — resuming from publication {data['next_idx']} / {data['total']}")
            return data
        except Exception as e:
            print(f"⚠️  Could not read checkpoint: {e}. Starting fresh.")
    return None

def save_checkpoint(next_idx, total, journals, conferences, preprints_list):
    """Persist progress so a crash can be resumed."""
    try:
        with open(CHECKPOINT_FILE, 'w') as f:
            json.dump({
                'next_idx': next_idx,
                'total': total,
                'journal_papers': journals,
                'conference_papers': conferences,
                'preprints': preprints_list,
                'saved_at': datetime.now().isoformat()
            }, f, indent=2, ensure_ascii=False)
    except Exception as e:
        print(f"  ⚠️  Could not save checkpoint: {e}")

def clear_checkpoint():
    if os.path.exists(CHECKPOINT_FILE):
        os.remove(CHECKPOINT_FILE)

# ----------------------------
# FETCH AUTHOR PROFILE
# ----------------------------
print("🔍 Fetching author profile from Google Scholar...")

author = None
for attempt in range(MAX_RETRIES):
    try:
        print(f"  Attempt {attempt + 1}/{MAX_RETRIES}...")
        if attempt > 0:
            wait = 2 ** attempt
            print(f"  Waiting {wait}s...")
            time.sleep(wait)
            renew_tor_circuit()

        search_query = scholarly.search_author_id(SCHOLAR_ID)
        if search_query is None:
            print("  ⚠️  Got None, retrying...")
            continue

        author = scholarly.fill(search_query, sections=["publications"])
        if author and 'publications' in author:
            print(f"  ✅ Found {len(author['publications'])} publications\n")
            break
        else:
            print("  ⚠️  No publications found, retrying...")
            author = None

    except Exception as e:
        print(f"  ⚠️  Error: {e}")
        if attempt == MAX_RETRIES - 1:
            print(f"\n❌ Failed after {MAX_RETRIES} attempts: {e}")
            exit(1)

if author is None:
    print("\n❌ Could not fetch author data")
    exit(1)

# ----------------------------
# HELPER FUNCTIONS
# ----------------------------
def safe_str(value):
    return "" if value is None else str(value).strip()

def random_delay():
    delay = random.uniform(MIN_DELAY, MAX_DELAY)
    time.sleep(delay)

def is_preprint(venue):
    return any(k in venue.lower() for k in PREPRINT_KEYWORDS)

def is_conference(venue):
    return any(k in venue.lower() for k in CONFERENCE_KEYWORDS)

def format_authors_initials(authors_str):
    if not authors_str:
        return ""
    authors = [a.strip() for a in re.split(r'\s+and\s+', authors_str)]
    formatted = []
    for name in authors:
        parts = name.split()
        if len(parts) == 1:
            formatted.append(parts[0])
        else:
            initials = [p[0] + "." for p in parts[:-1]]
            formatted.append(" ".join(initials) + " " + parts[-1])
    if len(formatted) > 1:
        return ", ".join(formatted[:-1]) + ", and " + formatted[-1]
    return formatted[0]

def format_journal_entry_cv_style(pub_data):
    authors = format_authors_initials(pub_data['authors'])
    entry = f'{authors}. "{pub_data["title"]}" **{pub_data["venue"]}**'
    if pub_data.get('volume'):
        entry += f' {pub_data["volume"]}'
    if pub_data.get('year'):
        entry += f' ({pub_data["year"]})'
    if pub_data.get('pages'):
        entry += f': {pub_data["pages"]}'
    return entry + '.'

def format_conference_entry_cv_style(pub_data):
    authors = format_authors_initials(pub_data['authors'])
    entry = f'{authors}. "{pub_data["title"]}" **{pub_data["venue"]}**'
    if pub_data.get('year'):
        entry += f' ({pub_data["year"]})'
    return entry + '.'

def check_if_in_cv(title, existing_cv_text):
    if not existing_cv_text:
        return False
    clean_title = re.sub(r'[^\w\s]', '', title.lower())
    clean_cv = re.sub(r'[^\w\s]', '', existing_cv_text.lower())
    title_words = set(clean_title.split())
    if not title_words:
        return False
    matches = sum(1 for w in title_words if w in clean_cv and len(w) > 3)
    return (matches / len(title_words)) > 0.8

def process_publication(idx, pub, total, existing_cv_text):
    """Fetch one publication with retries + circuit renewal on block."""
    for attempt in range(MAX_RETRIES):
        try:
            print(f"[{idx}/{total}] Fetching...", end="", flush=True)
            full_pub = scholarly.fill(pub)
            bib = full_pub.get("bib", {})

            title   = safe_str(bib.get("title"))
            venue   = safe_str(bib.get("venue") or bib.get("journal") or bib.get("citation"))
            year    = safe_str(bib.get("pub_year"))
            authors = safe_str(bib.get("author"))
            volume  = safe_str(bib.get("volume"))
            pages   = safe_str(bib.get("pages"))
            publisher = safe_str(bib.get("publisher"))

            if not title:
                print(" ⚠️  No title, skipped")
                return None

            if check_if_in_cv(title, existing_cv_text):
                print(f" ⏭️  Already in CV: {title[:40]}...")
                return None

            pub_data = {
                'title': title, 'authors': authors, 'venue': venue,
                'year': year, 'volume': volume, 'pages': pages,
                'publisher': publisher,
                'scholar_url': full_pub.get('pub_url', ''),
                'citations': full_pub.get('num_citations', 0)
            }

            if is_preprint(venue):
                pub_type, category = "PREPRINT", "preprint"
            elif is_conference(venue):
                pub_type, category = "CONFERENCE", "conference"
            else:
                pub_type, category = "JOURNAL", "journal"

            print(f" ✅ {year} - {title[:40]}... ({pub_type})")
            random_delay()
            return (pub_data, category)

        except Exception as e:
            err_str = str(e)
            if attempt < MAX_RETRIES - 1:
                print(f" ⚠️  Retry {attempt + 1}/{MAX_RETRIES}...", end="", flush=True)
                # ← NEW: renew circuit on block, not just wait
                if "Cannot Fetch" in err_str or "blocked" in err_str.lower() or attempt >= 1:
                    print()
                    renew_tor_circuit()
                else:
                    time.sleep(2 ** attempt)
            else:
                print(f" ❌ Failed after {MAX_RETRIES} attempts: {e}")
                return None

# ----------------------------
# LOAD EXISTING CV
# ----------------------------
print("📄 Loading existing CV to check for duplicates...")
existing_cv_text = ""
for filename in ['cv_draft.txt', 'CV.txt', 'vita.txt', 'faculty_vita.txt']:
    try:
        with open(filename, 'r', encoding='utf-8') as f:
            existing_cv_text = f.read()
        print(f"✅ CV loaded from {filename}\n")
        break
    except FileNotFoundError:
        continue
else:
    print("⚠️  No CV file found — will process all publications.\n")

# ----------------------------
# LOAD CHECKPOINT OR START FRESH
# ----------------------------
checkpoint = load_checkpoint()
total = len(author["publications"])

if checkpoint and checkpoint.get('total') == total:
    start_idx   = checkpoint['next_idx']        # 0-based index to resume at
    journal_papers    = checkpoint['journal_papers']
    conference_papers = checkpoint['conference_papers']
    preprints         = checkpoint['preprints']
    print(f"▶️  Resuming from [{start_idx + 1}/{total}]\n")
else:
    if checkpoint:
        print("⚠️  Checkpoint total mismatch (Scholar count changed?) — starting fresh\n")
    start_idx         = 0
    journal_papers    = []
    conference_papers = []
    preprints         = []

# ----------------------------
# PROCESS PUBLICATIONS
# ----------------------------
print("=" * 70)
print(f"🚀 Processing {total} publications...\n")
start_time = time.time()

for idx, pub in enumerate(author["publications"], 1):
    # Skip already-processed entries
    if idx - 1 < start_idx:
        continue

    result = process_publication(idx, pub, total, existing_cv_text)
    if result:
        pub_data, category = result
        if category == "preprint":
            preprints.append(pub_data)
        elif category == "conference":
            conference_papers.append(pub_data)
        else:
            journal_papers.append(pub_data)

    # ← NEW: save checkpoint after every publication
    save_checkpoint(idx, total, journal_papers, conference_papers, preprints)

    # ← NEW: proactively renew circuit every N pubs to stay ahead of blocks
    if idx % CIRCUIT_RENEW_AFTER == 0:
        print(f"\n  🔄 Proactive circuit renewal at [{idx}/{total}]...")
        renew_tor_circuit()

elapsed = time.time() - start_time
print("\n" + "=" * 70)

# ----------------------------
# SORT BY YEAR
# ----------------------------
journal_papers.sort(key=lambda x: x.get('year', '0'), reverse=True)
conference_papers.sort(key=lambda x: x.get('year', '0'), reverse=True)
preprints.sort(key=lambda x: x.get('year', '0'), reverse=True)

# ----------------------------
# GENERATE CV OUTPUT
# ----------------------------
print("\n💾 Generating CV-formatted additions...")
cv_output = [
    "=" * 70,
    "NEW PUBLICATIONS TO ADD TO CV",
    "=" * 70,
    f"Generated on: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
    f"From Google Scholar ID: {SCHOLAR_ID}",
    ""
]

if journal_papers:
    cv_output += [
        "\n" + "=" * 70,
        "JOURNAL PAPERS",
        "Add to Section: II.A.1 - Articles in Peer-Reviewed Journals",
        "=" * 70,
        "\nDuring ISU appointment\n",
    ]
    for pub in journal_papers:
        cv_output += [format_journal_entry_cv_style(pub), ""]

if conference_papers:
    cv_output += [
        "\n" + "=" * 70,
        "CONFERENCE PAPERS",
        "Add to Section: II.A.3 - Peer-Reviewed Conference Proceedings",
        "=" * 70,
        "\nDuring ISU appointment\n",
    ]
    for pub in conference_papers:
        cv_output += [format_conference_entry_cv_style(pub), ""]

if preprints:
    cv_output += [
        "\n" + "=" * 70,
        "PREPRINTS (ArXiv, etc.)",
        "=" * 70, ""
    ]
    for pub in preprints:
        cv_output += [format_conference_entry_cv_style(pub), ""]

cv_output += [
    "\n" + "=" * 70,
    "MANUAL REVIEW REQUIRED",
    "=" * 70, "",
    "Before adding to your CV, please:",
    "1. Mark graduate students with + after their names",
    "2. Mark undergraduate students with * after their names",
    "3. Verify all author names and initials",
    "4. Check volume and page numbers for accuracy",
    "5. Verify journal/conference names",
    "6. Add numbering to each section",
    "7. Update the CV date at the top of the document",
    "8. Note: Journal/conference names are in **bold** (use Word bold formatting)",
    ""
]

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
        'total_found': total,
        'new_journals': len(journal_papers),
        'new_conferences': len(conference_papers),
        'new_preprints': len(preprints),
        'processing_time_seconds': round(elapsed, 2)
    }
}
with open(OUTPUT_JSON, 'w', encoding='utf-8') as f:
    json.dump(json_output, f, indent=2, ensure_ascii=False)

# ← Clean up checkpoint on successful completion
clear_checkpoint()

# ----------------------------
# SUMMARY
# ----------------------------
print("\n✨ SYNC COMPLETE!\n")
print("📊 Summary:")
print(f"   ⏱️  Time: {elapsed:.1f}s")
print(f"   📚 New Journal Papers:    {len(journal_papers)}")
print(f"   📄 New Conference Papers: {len(conference_papers)}")
print(f"   📝 New Preprints:         {len(preprints)}")
print(f"   ✅ Total New:             {len(journal_papers) + len(conference_papers) + len(preprints)}")
print(f"\n📝 Output files: {OUTPUT_CV_FORMAT}, {OUTPUT_JSON}")
print("\n" + "=" * 70)
print("✅ Done!")
print("=" * 70)
