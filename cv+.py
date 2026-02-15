import socket
import time
import re
import random
import json
import os
import requests
from datetime import datetime
from scholarly import scholarly, ProxyGenerator

# ----------------------------
# CONFIGURATION
# ----------------------------
SCHOLAR_ID           = "-rmRjqIAAAAJ"
CV_LAST_UPDATE_DATE  = "2025-10-05"
OUTPUT_JSON          = "publications.json"
OUTPUT_CV_FORMAT     = "cv_formatted.txt"
CHECKPOINT_FILE      = "checkpoint.json"
MIN_DELAY            = 8.0
MAX_DELAY            = 15.0
MAX_RETRIES          = 8
CIRCUIT_RENEW_AFTER  = 15   # proactively rotate every N publications
MAX_PROBE_ATTEMPTS   = 15   # max circuit rotations hunting for a clean node

CONFERENCE_KEYWORDS = [
    'conference', 'proceedings', 'workshop', 'symposium', 'meeting',
    'nips', 'neurips', 'icml', 'cvpr', 'iccv', 'eccv', 'aaai', 'ijcai',
    'iclr', 'acm', 'ieee', 'iccps', 'acc', 'cdc', 'dscc', 'mecc',
    'allerton', 'siam', 'asilomar', 'hpec'
]
PREPRINT_KEYWORDS = ['arxiv', 'preprint', 'biorxiv', 'medrxiv', 'ssrn']

TOR_PROXIES = {
    'http':  'socks5h://127.0.0.1:9050',
    'https': 'socks5h://127.0.0.1:9050',
}

# ----------------------------
# TOR CIRCUIT RENEWAL
# ----------------------------
def renew_tor_circuit(silent=False):
    """Send NEWNYM to Tor control port via raw TCP (no stem needed)."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(10)
        s.connect(('127.0.0.1', 9051))
        s.sendall(b'AUTHENTICATE ""\r\nSIGNAL NEWNYM\r\nQUIT\r\n')
        resp = s.recv(1024).decode()
        s.close()
        if '250' in resp:
            if not silent:
                print("  🔄 Tor circuit renewed — waiting for new exit node...")
            time.sleep(7)
            return True
        else:
            if not silent:
                print(f"  ⚠️  Control port unexpected response: {resp[:60]}")
            return False
    except Exception as e:
        if not silent:
            print(f"  ⚠️  Circuit renewal failed: {e}")
        return False

def get_exit_ip():
    """Return current Tor exit IP, or None on failure."""
    try:
        r = requests.get('https://api.ipify.org',
                         proxies=TOR_PROXIES, timeout=15)
        return r.text.strip()
    except Exception:
        return None

# ----------------------------
# SETUP TOR PROXY IN scholarly
# ----------------------------
print("🔧 Configuring Tor proxy...")

# Tell scholarly to route through Tor via ProxyGenerator
# This is more reliable than env vars alone
pg = ProxyGenerator()
pg.Tor_Internal(tor_sock_port=9050, tor_control_port=9051)
scholarly.use_proxy(pg)

# Also set env vars as belt-and-suspenders fallback
os.environ['http_proxy']  = 'socks5h://127.0.0.1:9050'
os.environ['https_proxy'] = 'socks5h://127.0.0.1:9050'
os.environ['HTTP_PROXY']  = 'socks5h://127.0.0.1:9050'
os.environ['HTTPS_PROXY'] = 'socks5h://127.0.0.1:9050'

# Prevent scholarly from trying to download geckodriver at runtime
os.environ['WDM_LOCAL'] = '1'

print("  ✅ Tor proxy configured!\n")

# ----------------------------
# FIND A CLEAN EXIT NODE
# ----------------------------
def scholar_is_reachable():
    """Return True if Google Scholar responds without a block page."""
    try:
        r = requests.get(
            'https://scholar.google.com',
            proxies=TOR_PROXIES,
            timeout=20,
            headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; rv:109.0) Gecko/20100101 Firefox/115.0'}
        )
        text = r.text.lower()
        blocked = ('unusual traffic' in text or
                   'captcha' in text or
                   r.status_code in (429, 503))
        return not blocked
    except Exception:
        return False

print("🔍 Finding a clean Tor exit node for Google Scholar...")
clean_node = False
for probe in range(1, MAX_PROBE_ATTEMPTS + 1):
    ip = get_exit_ip()
    print(f"  Probe {probe}/{MAX_PROBE_ATTEMPTS} — exit IP: {ip} ...", end="", flush=True)
    if scholar_is_reachable():
        print(" ✅ Clean node!")
        clean_node = True
        break
    else:
        print(" ❌ Blocked, rotating...")
        renew_tor_circuit(silent=True)

if not clean_node:
    print("⚠️  Could not find a clean node after probing — proceeding anyway (may fail)\n")
else:
    print()

# ----------------------------
# CHECKPOINT HELPERS
# ----------------------------
def load_checkpoint():
    if os.path.exists(CHECKPOINT_FILE):
        try:
            with open(CHECKPOINT_FILE, 'r') as f:
                data = json.load(f)
            print(f"📂 Checkpoint found — resuming from index {data['next_idx']} / {data['total']}")
            return data
        except Exception as e:
            print(f"⚠️  Could not read checkpoint ({e}) — starting fresh.")
    return None

def save_checkpoint(next_idx, total, journals, conferences, preprints_list):
    try:
        with open(CHECKPOINT_FILE, 'w') as f:
            json.dump({
                'next_idx':        next_idx,
                'total':           total,
                'journal_papers':  journals,
                'conference_papers': conferences,
                'preprints':       preprints_list,
                'saved_at':        datetime.now().isoformat()
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
            print(f"  Rotating circuit and waiting...")
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
            print("  ⚠️  No publications in result, retrying...")
            author = None

    except Exception as e:
        print(f"  ⚠️  Error: {e}")
        if attempt == MAX_RETRIES - 1:
            print(f"\n❌ Failed after {MAX_RETRIES} attempts: {e}")
            # Write an empty checkpoint so the cache step doesn't warn
            save_checkpoint(0, 0, [], [], [])
            exit(1)

if author is None:
    print("\n❌ Could not fetch author data")
    save_checkpoint(0, 0, [], [], [])
    exit(1)

# ----------------------------
# HELPER FUNCTIONS
# ----------------------------
def safe_str(value):
    return "" if value is None else str(value).strip()

def random_delay():
    time.sleep(random.uniform(MIN_DELAY, MAX_DELAY))

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
    clean_cv    = re.sub(r'[^\w\s]', '', existing_cv_text.lower())
    title_words = set(clean_title.split())
    if not title_words:
        return False
    matches = sum(1 for w in title_words if w in clean_cv and len(w) > 3)
    return (matches / len(title_words)) > 0.8

def process_publication(idx, pub, total, existing_cv_text):
    """Fetch one publication with retries and circuit renewal on block."""
    for attempt in range(MAX_RETRIES):
        try:
            print(f"[{idx}/{total}] Fetching...", end="", flush=True)
            full_pub = scholarly.fill(pub)
            bib = full_pub.get("bib", {})

            title     = safe_str(bib.get("title"))
            venue     = safe_str(bib.get("venue") or bib.get("journal") or bib.get("citation"))
            year      = safe_str(bib.get("pub_year"))
            authors   = safe_str(bib.get("author"))
            volume    = safe_str(bib.get("volume"))
            pages     = safe_str(bib.get("pages"))
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
                'citations':   full_pub.get('num_citations', 0)
            }

            if is_preprint(venue):
                pub_type, category = "PREPRINT",  "preprint"
            elif is_conference(venue):
                pub_type, category = "CONFERENCE", "conference"
            else:
                pub_type, category = "JOURNAL",    "journal"

            print(f" ✅ {year} - {title[:40]}... ({pub_type})")
            random_delay()
            return (pub_data, category)

        except Exception as e:
            err_str = str(e)
            if attempt < MAX_RETRIES - 1:
                print(f"\n  ⚠️  [{idx}] Attempt {attempt+1} failed: {err_str[:60]}")
                # Always renew circuit on any fetch failure
                renew_tor_circuit()
            else:
                print(f"\n  ❌ [{idx}] Failed after {MAX_RETRIES} attempts: {err_str[:80]}")
                return None

# ----------------------------
# LOAD EXISTING CV
# ----------------------------
print("📄 Loading existing CV for duplicate checking...")
existing_cv_text = ""
for filename in ['cv_draft.txt', 'CV.txt', 'vita.txt', 'faculty_vita.txt']:
    try:
        with open(filename, 'r', encoding='utf-8') as f:
            existing_cv_text = f.read()
        print(f"  ✅ Loaded from {filename}\n")
        break
    except FileNotFoundError:
        continue
else:
    print("  ⚠️  No CV file found — all publications will be processed.\n")

# ----------------------------
# LOAD CHECKPOINT OR START FRESH
# ----------------------------
checkpoint = load_checkpoint()
total      = len(author["publications"])

if checkpoint and checkpoint.get('total') == total:
    start_idx         = checkpoint['next_idx']
    journal_papers    = checkpoint['journal_papers']
    conference_papers = checkpoint['conference_papers']
    preprints         = checkpoint['preprints']
    print(f"▶️  Resuming from [{start_idx + 1}/{total}]\n")
else:
    if checkpoint:
        print("⚠️  Checkpoint total mismatch — starting fresh\n")
    start_idx         = 0
    journal_papers    = []
    conference_papers = []
    preprints         = []

# Write an initial checkpoint immediately so the file exists
# even if the script fails on the very first publication
save_checkpoint(start_idx, total, journal_papers, conference_papers, preprints)

# ----------------------------
# PROCESS PUBLICATIONS
# ----------------------------
print("=" * 70)
print(f"🚀 Processing {total} publications (starting at {start_idx + 1})...\n")
start_time = time.time()

for idx, pub in enumerate(author["publications"], 1):
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

    # Save progress after every single publication
    save_checkpoint(idx, total, journal_papers, conference_papers, preprints)

    # Proactive circuit renewal every N publications
    if idx % CIRCUIT_RENEW_AFTER == 0:
        print(f"\n  🔄 Proactive rotation at [{idx}/{total}]...")
        renew_tor_circuit()

elapsed = time.time() - start_time
print("\n" + "=" * 70)

# ----------------------------
# SORT BY YEAR (newest first)
# ----------------------------
journal_papers.sort(   key=lambda x: x.get('year', '0'), reverse=True)
conference_papers.sort(key=lambda x: x.get('year', '0'), reverse=True)
preprints.sort(        key=lambda x: x.get('year', '0'), reverse=True)

# ----------------------------
# GENERATE CV-FORMATTED OUTPUT
# ----------------------------
print("\n💾 Generating CV-formatted output...")
cv_output = [
    "=" * 70,
    "NEW PUBLICATIONS TO ADD TO CV",
    "=" * 70,
    f"Generated on: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
    f"Scholar ID: {SCHOLAR_ID}",
    "",
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
        "=" * 70, "",
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
    "4. Check volume and page numbers",
    "5. Verify journal/conference names",
    "6. Add section numbering",
    "7. Update the CV date",
    "8. **Bold** markers = use Word bold formatting",
    "",
]

with open(OUTPUT_CV_FORMAT, 'w', encoding='utf-8') as f:
    f.write('\n'.join(cv_output))

# ----------------------------
# SAVE JSON
# ----------------------------
json_output = {
    'generated_date':    datetime.now().isoformat(),
    'cv_last_update':    CV_LAST_UPDATE_DATE,
    'scholar_id':        SCHOLAR_ID,
    'journal_papers':    journal_papers,
    'conference_papers': conference_papers,
    'preprints':         preprints,
    'statistics': {
        'total_found':              total,
        'new_journals':             len(journal_papers),
        'new_conferences':          len(conference_papers),
        'new_preprints':            len(preprints),
        'processing_time_seconds':  round(elapsed, 2),
    }
}
with open(OUTPUT_JSON, 'w', encoding='utf-8') as f:
    json.dump(json_output, f, indent=2, ensure_ascii=False)

# Remove checkpoint on clean completion
clear_checkpoint()

# ----------------------------
# SUMMARY
# ----------------------------
print("\n✨ SYNC COMPLETE!\n")
print(f"   ⏱️  Time:              {elapsed:.1f}s")
print(f"   📚 New journals:      {len(journal_papers)}")
print(f"   📄 New conferences:   {len(conference_papers)}")
print(f"   📝 New preprints:     {len(preprints)}")
print(f"   ✅ Total new:         {len(journal_papers) + len(conference_papers) + len(preprints)}")
print(f"\n   📁 {OUTPUT_CV_FORMAT}")
print(f"   📁 {OUTPUT_JSON}")
print("\n" + "=" * 70)
