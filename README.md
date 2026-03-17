# Signal Found MCP

**The only AI tool that connects directly to a proprietary Reddit outreach network — find your prospects, personalize your pitch, and send thousands of DMs per day.**

[![MCP Compatible](https://img.shields.io/badge/MCP-Compatible-blue)](https://modelcontextprotocol.io)
[![PyPI](https://img.shields.io/pypi/v/sf-mcp)](https://pypi.org/project/sf-mcp/)
[![Python 3.11+](https://img.shields.io/badge/Python-3.11%2B-green)](https://python.org)
[![smithery badge](https://smithery.ai/badge/sf-mcp)](https://smithery.ai/server/sf-mcp)

---

## Table of Contents

- [What is Signal Found?](#what-is-signal-found)
- [Two Ways to Operate](#two-ways-to-operate)
- [Quick Setup](#quick-setup)
  - [Claude Desktop](#claude-desktop)
  - [Claude Code (CLI)](#claude-code-cli)
  - [Cursor](#cursor)
  - [VS Code (GitHub Copilot)](#vs-code-github-copilot)
  - [Windsurf](#windsurf)
  - [Cline](#cline-vs-code-extension)
  - [Smithery](#smithery)
- [Local Install (Alternative)](#local-install-alternative)
- [How It Works](#how-it-works)
- [Available Tools](#available-tools)
- [Pricing & Credits](#pricing--credits)
- [Chrome Extension](#chrome-extension)
- [Configuration Reference](#configuration-reference)
- [Support](#support)

---

## What is Signal Found?

Signal Found is a Reddit-native outreach platform. You describe your product, we find people on Reddit already asking for it, and your AI agent handles the rest — messaging prospects, tracking replies, and optimizing your funnel in real time.

This MCP server gives **Claude, Cursor, VS Code Copilot, Windsurf**, and any other MCP-compatible AI agent direct access to the Signal Found platform. Your agent can:

- **Set up your product** and targeting strategy (subreddits, keywords, positioning)
- **Find prospects** already posting about problems your product solves
- **Send personalized DMs** at scale — hundreds or thousands per day
- **Manage your CRM** — track replies, update conversion states, follow up
- **Analyze performance** — close rates, voice-of-customer reports, campaign health

No custom code. No API wrangling. Just tell your agent what you're selling.

---

## Two Ways to Operate

### DIY — Your Reddit Account
Use the **Signal Found Chrome Extension** to link your own Reddit account. You control the account; Signal Found handles finding and messaging prospects.

**[→ Install the Chrome Extension](https://onboard.signal-found.com/extensions/reddit)**

### Managed Bot Network — Scale to Thousands
Don't want to use your own account? We operate a private network of **hundreds of Reddit accounts** that send outreach on your behalf — fully managed, with volume that a single account simply can't reach.

**Contact [admin@signal-found.com](mailto:admin@signal-found.com) to get onboarded.**

---

## Quick Setup

**Easiest: use our hosted server — nothing to install.**

Get your `client_id` at [signal-found.com](https://signal-found.com), then pick your client below.

---

### Claude Desktop

Edit `claude_desktop_config.json`:
- **Mac:** `~/Library/Application Support/Claude/claude_desktop_config.json`
- **Windows:** `%APPDATA%\Claude\claude_desktop_config.json`

```json
{
  "mcpServers": {
    "signal-found": {
      "url": "https://mcp.signal-found.com/mcp"
    }
  }
}
```

Restart Claude Desktop, then tell it: `Login to Signal Found with client ID: <your-client-id>`

---

### Claude Code (CLI)

```bash
claude mcp add signal-found --transport http https://mcp.signal-found.com/mcp
```

Or add to `.mcp.json` in your project:

```json
{
  "mcpServers": {
    "signal-found": {
      "type": "http",
      "url": "https://mcp.signal-found.com/mcp"
    }
  }
}
```

---

### Cursor

Add to `.cursor/mcp.json` in your project (or `~/.cursor/mcp.json` globally):

```json
{
  "mcpServers": {
    "signal-found": {
      "url": "https://mcp.signal-found.com/mcp"
    }
  }
}
```

---

### VS Code (GitHub Copilot)

Add to `.vscode/mcp.json` in your project:

```json
{
  "servers": {
    "signal-found": {
      "type": "http",
      "url": "https://mcp.signal-found.com/mcp"
    }
  }
}
```

Or add via the VS Code command palette: `MCP: Add Server` → HTTP → paste `https://mcp.signal-found.com/mcp`

---

### Windsurf

Edit `~/.codeium/windsurf/mcp_config.json`:

```json
{
  "mcpServers": {
    "signal-found": {
      "serverUrl": "https://mcp.signal-found.com/mcp"
    }
  }
}
```

---

### Cline (VS Code Extension)

Open Cline settings → MCP Servers → Add Server → paste:

```
https://mcp.signal-found.com/mcp
```

---

### Smithery

One-click install at **[smithery.ai/server/sf-mcp](https://smithery.ai/server/sf-mcp)** — Smithery will prompt you for your `client_id` and handle the rest.

---

## Local Install (Alternative)

If you prefer to run the server locally rather than use the hosted deployment:

```bash
pip install sf-mcp
# or: uvx sf-mcp
```

Then use this config in any client above, replacing the `url` approach:

**Claude Desktop:**
```json
{
  "mcpServers": {
    "signal-found": {
      "command": "uvx",
      "args": ["sf-mcp"],
      "env": {
        "ONBOARD_API_CLIENT_ID": "your-client-id-here"
      }
    }
  }
}
```

**Cursor / VS Code / Windsurf:**
```json
{
  "mcpServers": {
    "signal-found": {
      "command": "uvx",
      "args": ["sf-mcp"],
      "env": {
        "ONBOARD_API_CLIENT_ID": "your-client-id-here"
      }
    }
  }
}
```

---

## How It Works

```
You describe your product
        ↓
Signal Found scans Reddit for people posting about
problems your product solves
        ↓
Your agent configures targeting (subreddits, keywords,
market positioning, conversion notes)
        ↓
Messages sent via your Chrome extension or
our managed account network
        ↓
Replies land in your Signal Found CRM
Your agent tracks them, follows up, and closes
```

---

## Available Tools

### Onboarding & Setup
| Tool | Description |
|------|-------------|
| `login_with_client_id` | Authenticate your session, check credit balance |
| `agent_quickstart` | Recommended agent workflow for zero-context onboarding |
| `create_new_account` | Create a new Signal Found client account |
| `create_new_product` | Register a product and start the onboarding flow |
| `get_onboarding_status` | Check onboarding completion for a product |
| `run_full_agentic_onboarding` | Let the agent run the full onboarding pipeline autonomously |

### Targeting & Strategy
| Tool | Description |
|------|-------------|
| `configure_targeting` | Set subreddits and keywords for a product |
| `configure_product_strategy` | Define market positioning and messaging strategy |
| `modify_subreddits` | Add or remove subreddits from targeting |
| `modify_keywords` | Add or remove keywords from targeting |
| `modify_market_positioning` | Update product positioning copy |
| `submit_agent_targeting` | Submit finalized targeting for campaign activation |

### Outreach & Messaging
| Tool | Description |
|------|-------------|
| `send_reddit_message` | Send DMs to prospects (cold, reply, or batch) |
| `onboarding_campaign_decision` | Approve or modify the agent's targeting recommendations |

### CRM & Pipeline
| Tool | Description |
|------|-------------|
| `crm_workbench` | Full CRM view — leads, conversations, states |
| `crm_customers_by_state` | Filter leads by conversion state |
| `crm_state_stats` | Conversion funnel stats |
| `change_crm_state` | Update a lead's conversion state |
| `get_conversation_by_id` | Fetch a specific conversation |
| `get_conversation_notes` | Get notes on a conversation |
| `modify_conversion_notes` | Update conversion/followup notes |
| `upsert_conversation_note` | Add or update a note on a conversation |

### Analytics & Reporting
| Tool | Description |
|------|-------------|
| `sales_control_tower` | Command-center summary: products, campaigns, close rate, recommendations |
| `portfolio_close_rate` | Aggregate close rate across all products |
| `voice_of_customer_report` | Synthesized report from real prospect responses |
| `compare_confirmed_vs_uninterested` | Side-by-side analysis to improve targeting |
| `get_deduped_crm_by_category` | Deduplicated lead list by category |

### Campaigns & Funnels
| Tool | Description |
|------|-------------|
| `list_campaigns` | List all campaigns and their health |
| `list_products` | List all products |
| `get_product_tree` | Full product + campaign hierarchy |
| `rebalance_resources_to_product` | Shift outreach capacity to best-performing product |
| `modify_funnels` | Adjust funnel configuration |

### Billing & Credits
| Tool | Description |
|------|-------------|
| `billing_and_credits` | Credit balance, history, and Stripe checkout URL generation |

---

## Pricing & Credits

Credits are consumed when Signal Found generates and sends outreach messages. One credit ≈ one message.

| Plan | Credits | Price |
|------|---------|-------|
| **Starter** | 1,000 credits | Buy via `billing_and_credits` tool |
| **Pro** | 7,000 credits | Buy via `billing_and_credits` tool |
| **Bot Network** | Unlimited | [admin@signal-found.com](mailto:admin@signal-found.com) |

When you run out of credits, any outreach tool will automatically provide direct Stripe checkout links — no need to leave your AI client.

---

## Chrome Extension

The Signal Found Chrome Extension connects your Reddit account to the platform. Install it, open Reddit, and your agent can start sending DMs immediately.

**[→ Install for Chrome](https://onboard.signal-found.com/extensions/reddit)**

---

## Configuration Reference

For local installs, copy `.env.example` to `.env`:

```env
# Signal Found API (production — no change needed)
ONBOARD_API_BASE_URL=https://onboard.signal-found.com

# Your client ID from signal-found.com
ONBOARD_API_CLIENT_ID=your-client-id-here

# Request timeout (seconds)
ONBOARD_API_TIMEOUT_SECONDS=60

# Transport: stdio for local, streamable-http for hosted
MCP_TRANSPORT=stdio
```

---

## Support

- **Website:** [signal-found.com](https://signal-found.com)
- **Email:** [admin@signal-found.com](mailto:admin@signal-found.com)
- **Bot network onboarding:** [admin@signal-found.com](mailto:admin@signal-found.com)

---

© 2025 Signal Found. All rights reserved.
