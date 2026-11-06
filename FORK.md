# Fork notes: write tools for the Google Ads MCP server

This fork adds **write tools** (campaigns, ad groups, keywords, ads, budgets,
targeting, schedules, shared negative lists, conversions...) on top of
Google's official, mostly read-only
[googleads/google-ads-mcp](https://github.com/googleads/google-ads-mcp). The
upstream README (`README.md`) still describes the upstream tools and setup;
this file covers what the fork adds.

All write tools are in their own files under `ads_mcp/tools/` (plus
`ads_mcp/mutate_helpers.py`), so merging upstream rarely conflicts. The only
edits to upstream files are the category list in `ads_mcp/config.py` and
`ads_mcp/tools_config.yaml`, and a pointer in `README.md`.

## Safety model

Every tool that changes the account uses upstream's **Propose & Confirm**
workflow:

1. Call with `validate_only=True` (the default). Google Ads runs a dry run; the
   tool returns human-readable `change_items` (old value next to the new one for
   updates) and a `checksum`. Nothing is changed.
2. Show the `change_items` to the person who owns the account and get an
   explicit yes.
3. Call again with `validate_only=False`, the **exact same parameters** and the
   `checksum`. If any parameter differs from what was approved, the call is
   rejected before the account is touched.

More defaults:

- New campaigns, ad groups and ads are created **PAUSED**.
- Anything that can spend money (enabling, raising a budget, changing the
  bidding strategy) says so in its description.
- Removals are marked as destructive (`destructiveHint`) and are a last
  resort: pausing is preferred and always available.
- Adjustments are percentages (`+20` = bid 20% higher) rather than raw
  multipliers.

## Tools added by this fork (35)

Names are the MCP names as the server exposes them (`<category>_<tool>`).
`R` = read-only, `D` = destructive.

| Category | Tools |
|---|---|
| `campaigns` | `create_campaign` (own new budget or an existing one; strategies `MANUAL_CPC`, `MAXIMIZE_CLICKS`, `MAXIMIZE_CONVERSIONS`, `MAXIMIZE_CONVERSION_VALUE`; goals `target_cpa`, `target_roas`, `max_cpc_limit`), `update_campaign`, `set_campaign_status`, `update_campaign_budget` |
| `budgets` | `create_campaign_budget` (shared by default), `update_budget`, `set_campaign_budget`, `remove_budget` (D) |
| `ad_groups` | `create_ad_group`, `update_ad_group` (status and/or CPC) |
| `ads` | `create_responsive_search_ad`, `update_ad_group_ad`, `replace_responsive_search_ad` (new ad + pause the old one atomically), `remove_ad_group_ad` (D) |
| `keywords` | `add_keywords`, `add_negative_keywords` (campaign or ad-group level), `update_keyword`, `remove_keywords` (D) |
| `shared_sets` | `create_negative_keyword_list` (list + words + campaign links in one request), `add_list_negative_keywords`, `remove_list_negative_keywords` (D), `attach_negative_keyword_list`, `detach_negative_keyword_list` (D), `remove_negative_keyword_list` (D) |
| `targeting` | `suggest_geo_targets` (R), `add_campaign_locations` (target or exclude), `add_campaign_languages`, `remove_campaign_criteria` (D; locations, languages, campaign negatives) |
| `scheduling` | `add_campaign_ad_schedule`, `set_campaign_device_bid_modifier`, `set_criterion_bid_modifier` (location, schedule window or device) |
| `asset_links` | `link_asset`, `unlink_asset` (D) for campaign, ad group or account level |
| `conversions` | `create_conversion_action`, `update_conversion_action` |

Inherited from upstream (7): `search_search`, `metadata_get_resource_metadata`,
`customers_list_accessible_customers`, `assets_create_asset`,
`assets_upload_video_asset`, `labels_manage_labels`,
`labels_manage_label_associations`.

Reading is done with `search_search` (GAQL), for example campaigns, budgets
(`campaign_budget`), shared lists (`shared_set`, `shared_criterion`,
`campaign_shared_set`), schedules and bid adjustments (`campaign_criterion`).

Notes that matter:

- **Ad schedules:** once a campaign has any schedule window, ads run *only*
  inside the scheduled windows.
- **Bidding goals** are accepted only together with their strategy
  (`target_cpa` with `MAXIMIZE_CONVERSIONS`, `target_roas` with
  `MAXIMIZE_CONVERSION_VALUE`, `max_cpc_limit` with `MAXIMIZE_CLICKS`). The
  conversion-based strategies need working conversion tracking.
- **Responsive search ad text cannot be edited** after creation. Use
  `replace_responsive_search_ad`. Expanded text ads can no longer be created or
  edited at all.
- A budget that is not *shared* can serve only one campaign.

## Deliberately not included

- **Keyword Planner** (`KeywordPlanIdeaService`, a `suggest_keyword_ideas`
  tool). It was built and removed: the API rejects it with
  `DEVELOPER_TOKEN_NOT_APPROVED` for projects on *explorer* access (it needs
  Basic or Standard). Do not re-add it unless that access has been granted. A
  note to this effect sits in `ads_mcp/config.py`.
- A raw "apply any mutate" escape hatch (it would bypass the typed validation).
- Billing, account access and linking services.

## Setup used with this fork

The server authenticates through Application Default Credentials. With a
refresh token for the `https://www.googleapis.com/auth/adwords` scope, create an
`authorized_user` file (keep it out of the repository, `chmod 600`):

```json
{
  "type": "authorized_user",
  "client_id": "<oauth client id>",
  "client_secret": "<oauth client secret>",
  "refresh_token": "<refresh token>"
}
```

Register it with Claude Code (adjust the path):

```bash
claude mcp add google-ads \
  -e GOOGLE_APPLICATION_CREDENTIALS=$HOME/.config/google-ads-mcp/adc.json \
  -e GOOGLE_ADS_LOGIN_CUSTOMER_ID=<manager or account id> \
  -- uv run --directory /path/to/this/checkout -m ads_mcp.server
```

- `GOOGLE_ADS_DEVELOPER_TOKEN` is optional in this server (it is only sent when
  set). The setup this fork was developed against ran without one, on explorer
  access.
- `login_customer_id`: set it to the manager (MCC) ID when reaching a client
  account through a manager; for an account accessed directly, use that
  account's own ID. A wrong value gives `User doesn't have permission to access
  customer`.
- Explorer access has a lower daily operation limit than Basic. A request with
  many operations (for example 100 keywords) counts every operation.

## Read-only mode

To expose only reading, disable every write category in `tools_config.yaml`
(the upstream README names only `labels` and `assets`; this fork has more).
`targeting` mixes one read-only lookup with writes, so keep just that tool with
the fine-grained form:

```yaml
namespaces:
  customers: true
  search: true
  metadata: true
  targeting:
    enabled: true
    enabled_tools:
      - suggest_geo_targets: true
  labels: false
  assets: false
  campaigns: false
  budgets: false
  ad_groups: false
  ads: false
  keywords: false
  shared_sets: false
  scheduling: false
  asset_links: false
  conversions: false
```

## Verification status

Be aware of how far each tool has been checked:

- **Unit tests** (`python -m unittest discover -s tests -p "*_test.py" -t .`)
  cover every tool. Service clients are mocked with the *real* generated
  signatures (`create_autospec`), so a call with an argument the real method
  does not accept fails in the tests. The first version of these tools passed
  mocked tests but failed against the API (`validate_only` is not a keyword of
  the service methods); that is why the specs matter.
- **Live `validate_only` dry runs** against a real account (accepted by Google):
  all write tools except those listed below.
- **Real commits:** only a throwaway shared negative-keyword list was created
  and removed. Everything else has been dry-run only. Treat the first real
  commit of each tool as unproven.
- **Not verified live at all:** `detach_negative_keyword_list` and
  `set_campaign_budget` success paths (the test account had no shared list or
  shared budget), `replace_responsive_search_ad` (no responsive search ad in the
  test account), ad-group-level `add_negative_keywords`.

## Working with the fork

```bash
git fetch upstream && git merge upstream/main     # take Google's updates
git remote set-url --push upstream DISABLED       # make pushing to Google impossible
```

Contributions upstream are not planned (upstream keeps writes minimal by
design).
