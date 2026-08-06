# Agent usage sink

Lucy, Tars, Grant Scout, Growth Plan Creator and Tally call the Anthropic API
directly, so they never write to `~/.claude/projects` and the counter cannot see
them. Each now self-reports token usage to a shared Supabase table and the counter
merges it in.

## Table

`public.agent_usage_events` in project `fkzzapflftxmjfabjjjl`
(`https://fkzzapflftxmjfabjjjl.supabase.co`). One row per API call:
`agent, model, input_tokens, output_tokens, cache_creation_input_tokens,
cache_read_input_tokens, meta, ts`.

RLS is ON with **no policies**, so anon and authenticated are denied outright.
Both the writers and the counter use the **service_role** key, which bypasses RLS.

## Environment variables

Set on every agent AND on the machine running the counter:

    USAGE_SINK_URL = https://fkzzapflftxmjfabjjjl.supabase.co
    USAGE_SINK_KEY = <service_role key for fkzzapflftxmjfabjjjl>

Where each one goes:

| Agent | Host | Where to set |
| --- | --- | --- |
| Lucy | Railway | service Variables |
| Tars | Railway | service Variables |
| Grant Scout | Vercel | Project Settings ▸ Environment Variables |
| Growth Plan Creator | Vercel | Project Settings ▸ Environment Variables |
| Tally | Vercel | Project Settings ▸ Environment Variables |
| claude-counter | this Mac | the launchd plist for `com.aiden.claude-counter` |

**Both are fail-open.** If either variable is unset, agents skip reporting and the
counter renders from local transcripts alone. Nothing breaks; the number is just
Claude-Code-only, exactly as before.

## What is NOT covered

Claude desktop and mobile app conversations. Those live server-side - the desktop
app stores no conversation records locally (checked: its IndexedDB holds 3 UUIDs and
no messages), and the Admin API needs an organization account, which this is not.
`~/Library/Application Support/Claude/plan-usage-history.json` does hold
account-level plan-consumption percentages sampled while the app is open, which is a
different axis (intensity, not volume) and is not merged here.
