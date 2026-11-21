# Telegram premium icon slots

The bot supports Telegram Bot API `icon_custom_emoji_id` on its primary inline
buttons. Open **Settings -> Premium icons**, choose a slot, then send either the
custom emoji itself or its `custom_emoji_id`. The bot validates the ID with
`getCustomEmojiStickers`, stores it in `FactorySetting`, and enables configured
icons. `/clear` removes the selected slot and `/cancel` exits setup.

If Telegram rejects custom button icons for the bot/account, the request is
retried once with the Unicode fallback. Do not send the bot token, sticker
`file_id`, or `file_unique_id`; only send a custom emoji or its
`custom_emoji_id`.

| Slot | Fallback | Used for |
|---|---|---|
| `studio` | ✨ | Main Studio workspace |
| `review` | ✅ | Main Review workspace |
| `analytics` | 📈 | Insights and analytics |
| `operations` | 🖥 | Operations workspace |
| `settings` | ⚙️ | Settings and advanced controls |
| `automation` | 🤖 | Automation actions |
| `original_creation` | ✨ | Original topic creation |
| `custom_series` | 🧠 | Custom series creator |
| `trends` | 🔥 | Trends and topic discovery |
| `clip_studio` | 🎞 | Universal Clipping Studio |
| `clip_vod` | ➕ | Submit a VOD/video URL |
| `clip_live` | 🔴 | Submit/capture a live source |
| `review_queue` | 📥 | Review and job queues |
| `pipeline_status` | 📊 | Pipeline status/dashboard |
| `quality_review` | 🔍 | Quality and clip review |
| `analytics_sync` | 🔄 | Analytics synchronization |
| `system_health` | 🖥 | API/worker health |
| `render_worker` | 🛠 | FFmpeg renderer controls |
| `niche` | 🎯 | Niche selection |
| `content_format` | 🎞 | Reel/Short/long-form format |
| `reel_profile` | 📸 | Reel profile settings |
| `providers` | 🧩 | Provider control center |
| `provider_script` | 📝 | Script provider |
| `provider_image` | 🖼 | Image provider |
| `provider_voice` | 🎙 | Voice provider |
| `provider_video` | 🎬 | AI-video provider |
| `credentials` | 🔐 | Encrypted API credentials |
| `audio_library` | 🎵 | BGM/SFX library |
| `accounts` | 🔗 | Channel/platform accounts |
| `publishing_destinations` | 📡 | YouTube/Instagram target |
| `daily_limit` | 📤 | Daily publication limit |
| `publishing_mode` | 🧪 | Mock/live publishing mode |
| `platform_guide` | 📖 | Platform setup guide |
| `youtube` | ▶️ | YouTube actions |
| `instagram` | 📸 | Instagram actions |
| `icon_theme` | 💎 | Premium icon settings |
| `refresh` | 🔄 | Refresh actions |
| `pause` | ⏸️ | Pause factory/stream |
| `resume` | ▶️ | Resume factory/stream |
| `back` | 🔙 | Back navigation |
| `add` | ➕ | Add/create actions |
| `approve` | ✅ | Approval actions |
| `reject` | ❌ | Rejection actions |
| `revise` | ✍️ | Revision/edit actions |
| `publish` | 📤 | Publish actions |
| `preview` | ▶️ | Preview media |
| `retry` | 🔁 | Retry failed work |
| `schedule` | ⏰ | Scheduling actions |
| `delete` | 🗑 | Delete/remove actions |
| `connect` | 🔗 | Connect account/provider |
| `verify` | ✅ | Verify configuration |
| `cancel` | ❌ | Cancel setup/job |

One semantic slot should use the same custom emoji everywhere. Provider-brand
icons and dynamic state marks (selected, ready, warning, failed) intentionally
remain Unicode so their meaning stays clear.
