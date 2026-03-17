# Signal Found MCP

**The only AI tool that connects directly to a proprietary Reddit outreach network — find your prospects, personalize your pitch, and send thousands of DMs per day.**

[![MCP Compatible](https://img.shields.io/badge/MCP-Compatible-blue)](https://modelcontextprotocol.io)
[![Python 3.11+](https://img.shields.io/badge/Python-3.11%2B-green)](https://python.org)

---

## What is Signal Found?

Signal Found is a Reddit-native B2C/B2B outreach platform. You describe your product, we find people on Reddit already asking for it, and your AI agent handles the rest — messaging prospects, tracking replies, and optimizing your funnel in real time.

This MCP server gives **Claude, Cursor, Copilot**, and any other MCP-compatible AI agent direct access to the Signal Found platform. Your agent can:

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

## Quick Start

### 1. Install

```bash
pip install sf-mcp
```

Or with `uv`:
```bash
uvx sf-mcp
```

### 2. Get Your Client ID

Sign up at [signal-found.com](https://signal-found.com) to get your `client_id`.

### 3. Add to Your AI Client

**Claude Desktop** (`~/.claude/claude_desktop_config.json`):
```json
{
  "mcpServers": {
    "signal-found": {
      "command": "sf-mcp",
      "env": {
        "ONBOARD_API_BASE_URL": "https://onboard.signal-found.com",
        "ONBOARD_API_CLIENT_ID": "your-client-id-here"
      }
    }
  }
}
```

**Cursor** (`.cursor/mcp.json` in your project):
```json
{
  "mcpServers": {
    "signal-found": {
      "command": "sf-mcp",
      "env": {
        "ONBOARD_API_BASE_URL": "https://onboard.signal-found.com",
        "ONBOARD_API_CLIENT_ID": "your-client-id-here"
      }
    }
  }
}
```

**Claude Code** (add to your project's `.mcp.json`):
```json
{
  "mcpServers": {
    "signal-found": {
      "command": "sf-mcp",
      "env": {
        "ONBOARD_API_BASE_URL": "https://onboard.signal-found.com",
        "ONBOARD_API_CLIENT_ID": "your-client-id-here"
      }
    }
  }
}
```

### 4. Tell Your Agent to Start

```
Login with my Signal Found client ID: <your-client-id>
Then run the onboarding quickstart for my product: <product description>
```

Your agent will handle the rest — product setup, targeting, and first outbound messages.

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
Messages are sent via your Chrome extension or
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
| `agent_quickstart` | Get the recommended agent workflow for zero-context onboarding |
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
| `billing_and_credits` | Credit balance, history, and optional Stripe checkout URL generation |

---

## Pricing & Credits

Credits are consumed when Signal Found generates outreach messages and sends DMs. One credit ≈ one message.

| Plan | Credits | Use Case |
|------|---------|----------|
| **Starter** | 1,000 credits | Testing and early outreach |
| **Pro** | 7,000 credits | Active campaigns at scale |
| **Bot Network** | Unlimited | Contact us — [admin@signal-found.com](mailto:admin@signal-found.com) |

Purchase credits anytime through the `billing_and_credits` tool — your agent will generate a direct Stripe checkout link.

When you run out of credits, any outreach tool will tell you exactly how to buy more without leaving your AI client.

---

## Configuration Reference

Copy `.env.example` to `.env` and fill in your values:

```env
# Signal Found API (production endpoint — no change needed)
ONBOARD_API_BASE_URL=https://onboard.signal-found.com

# Your client ID from signal-found.com
ONBOARD_API_CLIENT_ID=your-client-id-here

# Request timeout (seconds)
ONBOARD_API_TIMEOUT_SECONDS=60

# Transport: stdio for Claude Desktop/Cursor, streamable-http for hosted
MCP_TRANSPORT=stdio
```

> **Note:** For most users, only `ONBOARD_API_CLIENT_ID` needs to be set. The rest have sensible defaults.

---

## Chrome Extension

The Signal Found Chrome Extension connects your Reddit account to the platform. Install it, open Reddit, and your agent can start sending DMs immediately.

**[→ Install for Chrome](https://onboard.signal-found.com/extensions/reddit)**

---

## Support

- **Email:** [admin@signal-found.com](mailto:admin@signal-found.com)
- **Bot network onboarding:** [admin@signal-found.com](mailto:admin@signal-found.com)
- **App / billing:** [signal-found.com](https://signal-found.com)

---

© 2025 Signal Found. All rights reserved.
