# 🎓 gcr-sync

> **Personal Google Classroom Synchronization System**

Automatically sync your Google Classroom — coursework, materials, announcements, and attachments — to organized local folders. Get Telegram notifications for new content and optional AI-powered digests.

```
./sync.sh
```

```
🎓 Classroom Update

[DBMS 2026]

📄 New Material
Normalization.pdf

📝 New Assignment
SQL Lab 4
Due: 12 Jun

------------------

[Mathematics]

📢 New Announcement
Quiz postponed

------------------

🤖 Summary

DBMS contains a new assignment due on 12 Jun and a new reference
document. Mathematics contains a new announcement regarding a
postponed quiz.
```

**Only new items. No duplicates. No spam. No manual configuration.**

---

## ✨ Features

| Feature | Description |
|---------|-------------|
| 🔐 **OAuth Authentication** | Secure Google sign-in with automatic token refresh |
| 🔍 **Dynamic Course Discovery** | Automatically detects all active courses — no hardcoding |
| 📁 **Subject-wise Folders** | Organized `Assignments/`, `Materials/`, `Announcements/`, `Metadata/` per course |
| 📥 **Attachment Downloads** | PDF, DOCX, PPTX, XLSX, Google Docs/Sheets/Slides exports |
| 🗃️ **SQLite Tracking** | Persistent duplicate detection across runs |
| ⏰ **Overdue Filtering** | Past-due assignments are silently ignored |
| 📱 **Telegram Notifications** | Consolidated messages only when new content exists |
| 🤖 **AI Summaries** | Optional Groq-powered concise digests (metadata only) |
| 📝 **Structured Logging** | Console + file logging with configurable levels |
| 🛡️ **Error Resilience** | One failed file never crashes the entire sync |

---

## 🚀 Quick Start

### Prerequisites

- **Python 3.10+**
- A **Google Cloud** project with Classroom API and Drive API enabled
- A **Telegram bot** (created via [@BotFather](https://t.me/BotFather))
- *(Optional)* A [Groq API key](https://console.groq.com/keys) for AI summaries

### Setup

```bash
# Clone the repository
git clone <repo-url>
cd gcr-sync

# Create virtual environment (recommended)
python -m venv venv
source venv/bin/activate

# Install dependencies
pip install -r requirements.txt

# Configure environment
cp .env.example .env
# Edit .env with your credentials (see Configuration section below)
```

### Run

```bash
./sync.sh
```

On first run, a browser window opens for Google sign-in. Subsequent runs use cached credentials automatically.

---

## 🔧 Configuration

All configuration is via environment variables in the `.env` file.

### Required Variables

| Variable | Description |
|----------|-------------|
| `GOOGLE_CLIENT_ID` | Google OAuth 2.0 Client ID |
| `GOOGLE_CLIENT_SECRET` | Google OAuth 2.0 Client Secret |
| `TELEGRAM_BOT_TOKEN` | Telegram Bot API token (from @BotFather) |
| `TELEGRAM_CHAT_ID` | Your Telegram chat ID |

### Optional Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `GOOGLE_REDIRECT_URI` | `http://localhost:8080` | OAuth redirect URI |
| `ENABLE_AI_SUMMARY` | `true` | Enable Groq AI summaries |
| `GROQ_API_KEY` | — | Groq API key (required if AI enabled) |
| `GROQ_MODEL` | `llama-3.3-70b-versatile` | Groq model to use |
| `DATA_DIRECTORY` | `./subjects` | Base directory for downloads |
| `DATABASE_PATH` | `./cache.db` | SQLite database path |
| `LOG_LEVEL` | `INFO` | Logging level (`DEBUG`, `INFO`, `WARNING`, `ERROR`) |

---

## 🔑 Google Cloud Setup

1. Go to [Google Cloud Console](https://console.cloud.google.com/)
2. Create a new project (or select an existing one)
3. Navigate to **APIs & Services → Library**
4. Enable:
   - **Google Classroom API**
   - **Google Drive API**
5. Navigate to **APIs & Services → Credentials**
6. Click **Create Credentials → OAuth 2.0 Client ID**
7. Select **Desktop application** as the application type
8. Copy the **Client ID** and **Client Secret** into your `.env` file
9. Under **OAuth consent screen**, add your Google account as a test user

---

## 📱 Telegram Bot Setup

1. Open Telegram and message [@BotFather](https://t.me/BotFather)
2. Send `/newbot` and follow the prompts to create your bot
3. Copy the bot token → `TELEGRAM_BOT_TOKEN` in `.env`
4. Send any message to your new bot
5. Visit `https://api.telegram.org/bot<YOUR_TOKEN>/getUpdates`
6. Find your `chat_id` in the JSON response → `TELEGRAM_CHAT_ID` in `.env`

---

## 📂 Folder Structure

After syncing, the application creates organized directories per course:

```
subjects/
├── DBMS 2026/
│   ├── Assignments/          # Downloaded assignment attachments
│   ├── Materials/            # Downloaded course materials
│   ├── Announcements/        # Downloaded announcement attachments
│   └── Metadata/             # JSON metadata for each item
│
├── IV SEM - 22CD43 - DAA/
│   ├── Assignments/
│   ├── Materials/
│   ├── Announcements/
│   └── Metadata/
│
└── ... (auto-created for each course)
```

- **New courses** are automatically detected and folders created on the next sync
- **Folders are never deleted** — only created
- **Files are never overwritten** — duplicates get `_1`, `_2` suffixes

---

## 🗃️ Database Schema

The SQLite database (`cache.db`) tracks everything:

| Table | Purpose |
|-------|---------|
| `courses` | All discovered courses with sync timestamps |
| `seen_items` | Processed item IDs for duplicate detection (composite key: `item_id` + `course_id`) |
| `downloads` | File download history with success/failure tracking |
| `sync_history` | Sync operation logs with statistics |

---

## 🤖 AI Summary

When enabled, gcr-sync sends **only metadata** to Groq for summarization:

- ✅ Course names, item titles, due dates
- ❌ Never reads downloaded files
- ❌ Never scans PDFs or documents
- ❌ Never sends attachment contents

The AI generates a concise plain-text summary (≤100 words). If Groq fails, the sync continues normally and sends the notification without a summary.

---

## 🛡️ Error Handling

| Scenario | Behavior |
|----------|----------|
| Expired OAuth token | Automatically refreshed |
| API quota exceeded | Logged, continues with other courses |
| Network failure | Downloads retry 3× with backoff |
| Invalid credentials | Clear error message, exits |
| Failed download | Logged, continues with other files |
| Telegram failure | Logged, sync still completes |
| Groq failure | Logged, notification sent without summary |
| Malformed API data | Skipped with warning |

**The sync never crashes because one file or one course fails.**

---

## 🔒 Security

- All secrets loaded from environment variables
- No hardcoded API keys, tokens, or credentials
- `.gitignore` excludes sensitive files:
  - `.env` — credentials
  - `token.json` — OAuth tokens
  - `cache.db` — database
  - `logs/` — log files
  - `subjects/` — downloaded content

---

## 📁 Project Structure

```
gcr-sync/
├── src/
│   ├── __init__.py          # Package marker
│   ├── config.py            # Environment-based configuration
│   ├── logger.py            # Structured logging (console + file)
│   ├── models.py            # Dataclasses for all entities
│   ├── database.py          # SQLite persistence layer
│   ├── auth.py              # Google OAuth authentication
│   ├── classroom.py         # Google Classroom API client
│   ├── downloader.py        # Attachment download manager
│   ├── notifier.py          # Telegram notifications
│   ├── ai_summary.py        # Groq AI digest generation
│   └── main.py              # Main orchestrator
├── subjects/                # Downloaded content (gitignored)
├── logs/                    # Log files (gitignored)
├── cache.db                 # SQLite database (gitignored)
├── token.json               # OAuth token cache (gitignored)
├── requirements.txt         # Python dependencies
├── .env.example             # Environment template
├── .gitignore               # Git ignore rules
├── sync.sh                  # Entry point script
└── README.md                # This file
```

---

## 📋 How It Works

```
./sync.sh
    │
    ├── 1. Load .env configuration
    ├── 2. Authenticate with Google (OAuth + token cache)
    ├── 3. Discover all active courses
    ├── 4. For each course:
    │       ├── Fetch coursework, materials, announcements
    │       ├── Filter: skip seen items (SQLite lookup)
    │       ├── Filter: skip overdue assignments
    │       ├── Download attachments (with retry)
    │       ├── Save metadata as JSON
    │       └── Mark items as seen in database
    ├── 5. Generate AI summary (optional, Groq)
    ├── 6. Send Telegram notification (if new items exist)
    └── 7. Log sync statistics
```

---


