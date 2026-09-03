# Vyro Campaign Automation Engine

## রিপো স্ট্রাকচার

```
.
├── requirements_parser.py
├── renderer.py
├── metadata.py
├── checker.py
├── youtube_uploader.py
├── generate_youtube_token.py
├── vyro_client.py              ← NEW: Vyro লগইন/স্ক্র্যাপ/সাবমিট (Playwright)
├── daily_runner.py             ← NEW: পুরো 100% automation-এর entrypoint
├── main.py                     (ম্যানুয়াল/টেস্ট রানের জন্য এখনো কাজ করে)
├── processed_campaigns.json    ← NEW: কোন ক্যাম্পেইন সাবমিট হয়ে গেছে তার লগ
├── requirements.txt
├── campaign.json                (fallback/টেস্ট ডেটা, daily_runner এটা ব্যবহার করে না)
└── .github/workflows/
    ├── vyro_pipeline.yml        (পুরনো: ম্যানুয়াল ট্রিগার)
    └── vyro_daily.yml           ← NEW: প্রতিদিন cron দিয়ে ফুল অটোমেশন

```

## ⚠️ ধাপ ০ (বাধ্যতামূলক): Vyro সেশন কুকি বের করা

Vyro-তে পাসওয়ার্ড নেই — লগইন করতে ইমেইলে OTP আসে, তাই email+password bot দিয়ে
automate করা যায় না। এর বদলে আমরা তোমার **লগইন সেশন কুকি** (`vyro_sid`) একবার
ম্যানুয়ালি বের করে GitHub Secret-এ রাখব — বট রোজ এই কুকি দিয়েই সরাসরি
লগইন হয়ে যাবে, নতুন করে OTP লাগবে না।

**যেভাবে বের করবে (ফোন থেকেই সম্ভব, Kiwi Browser দিয়ে):**
1. Play Store থেকে **Kiwi Browser** ইনস্টল করো (এটাতে ডেস্কটপ Chrome-এর মতো DevTools আছে)
2. Kiwi দিয়ে `app.vyro.com` খুলে normal OTP দিয়ে লগইন করে dashboard-এ ঢোকো
3. উপরে **⋮ মেনু → Developer tools**
4. DevTools-এর ট্যাব বারে **"Application"** ট্যাবে যাও
5. বামপাশে **Storage → Cookies → https://app.vyro.com**-এ ট্যাপ করো
6. তালিকায় **`vyro_sid`** নামের কুকি খুঁজে বের করো, তার **Value**-এর উপর ট্যাপ করে ধরে রেখে **পুরো (untruncated) মান কপি করো**
7. GitHub রিপোর **Settings → Secrets and variables → Actions**-এ গিয়ে নতুন secret বানাও:
   - Name: `VYRO_SESSION_COOKIE`
   - Value: কপি করা `vyro_sid`-এর মান

**গুরুত্বপূর্ণ:** এই সেশন সাধারণত কয়েক সপ্তাহ/মাস কাজ করবে। মেয়াদ শেষ হলে
workflow লগে **"Vyro session expired"** এরর দেখাবে — তখন এই ধাপগুলো আবার
রিপিট করে শুধু secret-টা আপডেট করলেই চলবে, কোড বদলাতে হবে না।

আগে যদি `VYRO_EMAIL` / `VYRO_PASSWORD` নামে secret বানিয়ে থাকো, ওগুলো এখন আর
ব্যবহার হয় না — চাইলে মুছে ফেলতে পারো (রেখে দিলেও ক্ষতি নেই)।

## ⚠️ পরের ধাপ: বাকি Vyro সিলেক্টর বের করা

`vyro_client.py`-তে যেসব লাইনে `# TODO` কমেন্ট আছে, ওগুলো placeholder — আমি
তোমার লগইন করা Vyro ড্যাশবোর্ড নিজে দেখতে পারি না, তাই আসল HTML স্ট্রাকচার
জানি না। একবার নিজে বের করে দিতে হবে:

1. ফোন/কম্পিউটারে Chrome দিয়ে `app.vyro.com`-এ লগইন করো
2. যে ইনপুট বক্স/বাটন দরকার (email/password/submit/campaign card/video-url
   input) — তার উপর **Inspect** বা **Inspect Element** চাপো
3. হাইলাইট হওয়া HTML ট্যাগে right-click → **Copy → Copy selector**
4. `vyro_client.py`-এর সংশ্লিষ্ট `# TODO` লাইনে ওই selector বসাও

সহজ না লাগলে, ঐ পেজগুলোর HTML (Ctrl+U বা "View Page Source" / DevTools →
Elements ট্যাবের স্ক্রিনশট) আমাকে পাঠালে আমি নিজেই সঠিক selector বসিয়ে দেব।

## ধাপ ১: YouTube OAuth টোকেন বানানো (একবারই, নিজের কম্পিউটারে)

GitHub Actions-এ ব্রাউজার লগইন সম্ভব না, তাই এই ধাপটা লোকালি একবার করতে হবে:

1. https://console.cloud.google.com/apis/credentials -এ যান, একটা প্রজেক্ট বানান
2. "YouTube Data API v3" enable করুন
3. **Create Credentials → OAuth client ID → Application type: Desktop app**
4. ডাউনলোড করা JSON ফাইলটা রিপোর root-এ `client_secrets.json` নামে রাখুন (এই ফাইল **কখনো** GitHub-এ পুশ করবেন না — `.gitignore`-এ যোগ করুন)
5. রান করুন:

```bash
pip install -r requirements.txt
python generate_youtube_token.py
```

ব্রাউজার খুলবে, আপনার YouTube অ্যাকাউন্টে লগইন করে অনুমতি দিন। এতে `token.json` তৈরি হবে।

6. এই টোকেনটা GitHub Secret হিসেবে সেভ করুন:

```bash
base64 -w0 token.json   # Mac হলে: base64 -i token.json
```

আউটপুটটা কপি করে GitHub রিপোর **Settings → Secrets and variables → Actions → New repository secret** এ যোগ করুন, নাম দিন: `YOUTUBE_TOKEN_B64`।

> `token.json` এবং `client_secrets.json` — দুটোই `.gitignore`-এ রাখুন, কখনো রিপোতে কমিট করবেন না।

## ধাপ ২: লোকাল রান (নিজের কম্পিউটারে)

```bash
# ffmpeg + imagemagick (Ubuntu/Debian উদাহরণ)
sudo apt-get install -y ffmpeg imagemagick

python main.py \
  --campaign campaign.json \
  --source input_16x9.mp4 \
  --output output/short.mp4 \
  --hook "He gave away $100,000 in 60 seconds" \
  --summary "Clipped from the original livestream." \
  --privacy unlisted
```

রান শেষে টার্মিনালে ও `video_url.txt` ফাইলে ভিডিওর YouTube URL পাবেন।

## ধাপ ৩: GitHub Actions দিয়ে রান করা

1. সব ফাইল রিপোতে পুশ করুন (`token.json`/`client_secrets.json` বাদে)
2. `YOUTUBE_TOKEN_B64` secret সেট করা আছে কিনা নিশ্চিত করুন (উপরের ধাপ ১)
3. **Actions** ট্যাব → **Vyro Campaign Pipeline** → **Run workflow**, ইনপুট দিন:
   - `source_video_url`, `campaign_json_path`, `hook`, `summary`, `privacy` (public/unlisted/private)
4. রান শেষে:
   - **Job Summary**-তে ভিডিও URL সরাসরি দেখা যাবে
   - **Artifacts** থেকে `video-url` (txt ফাইল) ও `vyro-short` (রেন্ডার করা mp4) ডাউনলোড করা যাবে

## ধাপ ৪: Vyro-তে সাবমিট (ম্যানুয়াল)

Vyro-র কোনো পাবলিক সাবমিশন API নেই — তারা শুধু ওয়েব ড্যাশবোর্ডে লগইন করে ম্যানুয়ালি clip URL সাবমিট করার সিস্টেম রেখেছে। তাই:

1. উপরের URL (job summary/artifact থেকে) কপি করুন
2. Vyro ড্যাশবোর্ডে গিয়ে সংশ্লিষ্ট ক্যাম্পেইনে লগইন করুন
3. URL পেস্ট করে নিজে সাবমিট করুন

এটা ইচ্ছাকৃতভাবে manual রাখা হয়েছে, কারণ Vyro-র সাইটে bot দিয়ে অটো-লগইন/ফর্ম-সাবমিট করানো তাদের Terms of Service ভঙ্গ করতে পারে।

## ধাপ ৫: ফুল ১০০% অটোমেশন সেটআপ (`daily_runner.py`)

এই ফ্লো: **Vyro চেক → ক্যাম্পেইন থাকলে রিকোয়ারমেন্ট + সোর্স ক্লিপ স্ক্র্যাপ →
ডাউনলোড → এডিট/রেন্ডার → YouTube আপলোড → Vyro-তে লিংক অটো-সাবমিট**, প্রতিদিন
নিজে থেকে চলবে GitHub Actions cron দিয়ে।

1. উপরের **ধাপ ০** এবং **ধাপ ১** (YouTube token) আগে শেষ করো
2. GitHub রিপোর **Settings → Secrets and variables → Actions**-এ এই secret গুলো যোগ করো:
   - `VYRO_SESSION_COOKIE` — তোমার Vyro-র `vyro_sid` কুকির মান (উপরে "ধাপ ০"-এ যেভাবে বের করেছ)
   - `YOUTUBE_TOKEN_JSON` — `token.json` ফাইলের পুরো কন্টেন্ট (সরাসরি JSON, base64 লাগবে না)
3. `.github/workflows/vyro_daily.yml` ফাইলে `cron: "0 6 * * *"` — চাইলে সময় বদলাও ([crontab.guru](https://crontab.guru) দিয়ে সহজে বানানো যায়)
4. রিপোতে সব ফাইল পুশ করো (`token.json`/`client_secrets.json` ছাড়া)
5. **Actions → Vyro Daily Full Automation → Run workflow** দিয়ে প্রথমবার ম্যানুয়ালি টেস্ট করো
6. Job লগ দেখো — কোনো selector fail করলে ঠিক কোন লাইনে সমস্যা সেটা এরর মেসেজে বলে দেবে

এরপর থেকে প্রতিদিন নিজে থেকে চলবে: ক্যাম্পেইন না থাকলে চুপচাপ স্কিপ করবে,
থাকলে পুরো পাইপলাইন চালিয়ে Vyro-তে লিংক জমা দিয়ে দেবে, এবং
`processed_campaigns.json`-এ লিখে রাখবে যাতে একই ক্যাম্পেইন দুইবার সাবমিট না হয়।

## ধাপ ৬: Instagram Reels আপলোড সেটআপ (সম্পন্ন)

`IG_ACCESS_TOKEN` আর `IG_BUSINESS_ACCOUNT_ID` secret ইতিমধ্যে সেট করা হয়েছে।
Instagram তার API দিয়ে সরাসরি ফাইল নেয় না — একটা পাবলিক URL থেকে ভিডিও
"টেনে" নেয়, তাই `instagram_uploader.py` রেন্ডার হওয়া ভিডিওকে সাময়িকভাবে
একটা **আলাদা পাবলিক GitHub রিপোতে** Release asset হিসেবে আপলোড করে,
সেই লিংক Instagram-কে দেয়, publish হওয়ার পর ওই temporary ফাইলটা থেকে যায়
(চাইলে periodically রিলিজ মুছে ফেলার automation পরে যোগ করা যাবে)।

**এই ২টা অতিরিক্ত secret লাগবে:**

1. GitHub-এ একটা নতুন **PUBLIC** রিপো বানাও (শুধু ভিডিও হোস্ট করার জন্য, কোনো কোড রাখতে হবে না) — যেমন নাম `vyro-media-host`
2. GitHub → প্রোফাইল ছবি → **Settings → Developer settings → Personal access tokens → Tokens (classic) → Generate new token**
   - Scope-এ শুধু **`repo`** টিক দাও
   - Expiration: চাইলে "No expiration" বা ১ বছর
   - Generate করে টোকেনটা কপি করে রাখো (একবারই দেখাবে)
3. মূল automation রিপোতে (Vyro-automation) দুটো secret যোগ করো:
   - `ASSET_HOST_REPO` — মান: `তোমার-ইউজারনেম/vyro-media-host` (উদাহরণ: `debashispaul2023-svg/vyro-media-host`)
   - `ASSET_HOST_TOKEN` — মান: উপরে বানানো Personal Access Token

এই দুটো সেট হলেই Instagram আপলোড ধাপ কাজ করবে। YouTube upload ব্যর্থ হলে পুরো
রান বন্ধ হয়ে যাবে, কিন্তু Instagram ধাপ ব্যর্থ হলে শুধু লগে এরর দেখাবে আর
বাকি পাইপলাইন (Vyro-তে লিংক সাবমিট) চালিয়ে যাবে — Instagram-কে "বোনাস"
চ্যানেল হিসেবে ধরা হয়েছে যাতে এটার সমস্যায় মূল YouTube+Vyro ফ্লো আটকে না যায়।

`IG_ACCESS_TOKEN` প্রতি ৬০ দিনে মেয়াদ শেষ হবে — তখন Meta Developer App →
Instagram → API setup with Instagram login → আবার "Generate token" চেপে
নতুন টোকেন দিয়ে secret আপডেট করতে হবে।

## ধাপ ৭: Whop সেটআপ (দ্বিতীয় ক্যাম্পেইন সোর্স)

Whop-এ Google দিয়ে লগইন করো বলে সরাসরি লগইন automate করা যায় না। এর বদলে
পুরো cookie জার (একসাথে সবগুলো cookie) কপি করে GitHub Secret-এ রাখতে হবে।

**যেভাবে বের করবে:**
1. Kiwi Browser দিয়ে whop.com-এ Google দিয়ে লগইন করো
2. DevTools → **Network** ট্যাব → পেজ রিফ্রেশ করো
3. whop.com-এ যাওয়া যেকোনো একটা request-এ ট্যাপ করো
4. **Headers** ট্যাবে **Request Headers** সেকশনে **"Cookie:"** লাইন খুঁজো
5. পুরো লম্বা মানটা (সব cookie সহ) কপি করো
6. GitHub secret: `WHOP_COOKIE_HEADER` — মান: এই পুরো cookie স্ট্রিং

**⚠️ সম্ভাব্য সমস্যা:** এই cookie-গুলোর একটা (`cf_clearance`) Cloudflare-এর
bot-protection pass-token, যেটা তোমার নির্দিষ্ট IP-র সাথে বাঁধা থাকতে পারে।
GitHub Actions সার্ভার ভিন্ন IP থেকে চলে বলে এটা কাজ নাও করতে পারে — চেষ্টা
করে দেখব, না হলে ভিন্ন সমাধান (residential proxy ইত্যাদি) লাগবে।

**⚠️ campaign scraping এখনো অসম্পূর্ণ:** `whop_client.py`-তে ক্যাম্পেইন
কার্ড/ফর্মের selector গুলো এখনো placeholder (`# TODO`), কারণ এখনো কোনো
active campaign Whop-এ নেই দেখার জন্য। ক্যাম্পেইন এলে DevTools স্ক্রিনশট
পাঠালে সেগুলো ঠিক করে দেওয়া হবে (Vyro-র জন্য যেভাবে করা হয়েছিল)।

`daily_runner.py` এখন প্রথমে Vyro চেক করবে, কিছু না পেলে Whop-ও চেক করবে,
যেখানে campaign পাবে সেখান থেকেই প্রসেস করে সেই একই প্ল্যাটফর্মে লিংক
সাবমিট করবে।

## ধাপ ৮: AI Brain সেটআপ (Claude API)

এখন থেকে টেমপ্লেটের বদলে Claude AI দিয়ে requirement বোঝা, title/caption
লেখা, আর campaign-এর মান যাচাই করা হবে। খরচ খুবই কম (সস্তা "Haiku" মডেল
ব্যবহার হচ্ছে) — প্রতিদিনের এই ছোট কাজের জন্য মাসে সম্ভবত কয়েক টাকার বেশি
হবে না, তবে নিশ্চিত হতে [console.anthropic.com](https://console.anthropic.com)-এ pricing দেখে নিও।

**সেটআপ:**
1. [console.anthropic.com](https://console.anthropic.com)-এ অ্যাকাউন্ট বানাও/লগইন করো
2. Billing-এ গিয়ে সামান্য কিছু ক্রেডিট যোগ করো (শুরুতে $5 যথেষ্ট, এই ব্যবহারে অনেক দিন চলবে)
3. **API Keys** সেকশনে গিয়ে নতুন একটা key বানাও
4. GitHub secret যোগ করো:
   - Name: `ANTHROPIC_API_KEY`
   - Value: এইমাত্র বানানো key

**AI brain যা করবে (`ai_brain.py`):**
- **Requirement parsing** — যেকোনো ভাষা/ফরম্যাটে লেখা ক্যাম্পেইন নিয়ম বুঝে হ্যাশট্যাগ/লিংক/duration/referral code বের করবে (আগের রেজেক্স-বেসড পার্সার এখন শুধু fallback হিসেবে থাকবে, AI ব্যর্থ হলে সেটা ব্যবহার হবে)
- **Metadata generation** — টেমপ্লেটের বদলে সত্যিকারের আকর্ষণীয় title/description/caption লিখবে, কিন্তু বাধ্যতামূলক hashtag/link ঠিকই থাকবে নিশ্চিত করা হয় (safety-check করে)
- **Campaign quality screening** — নতুন ক্যাম্পেইন পেলে প্রথমে AI দিয়ে যাচাই করবে সেটা legit মনে হচ্ছে কিনা (অস্পষ্ট/সন্দেহজনক শর্ত থাকলে স্কিপ করে অন্য প্ল্যাটফর্ম ট্রাই করবে — Vyro-তে খারাপ ক্যাম্পেইন পেলে Whop চেক করবে, বা উল্টো)

**এখনো বানানো হয়নি (পরের ধাপে):**
- Video থেকে automatically সেরা মুহূর্ত বেছে ক্লিপ করা (transcript লাগবে, আলাদা বড় কাজ)
- Comment-এ AI দিয়ে auto-reply

## গুরুত্বপূর্ণ সতর্কতা
- Vyro-তে bot দিয়ে auto-login/auto-submit করা তাদের Terms of Service ভঙ্গ করতে পারে। এটা সম্পূর্ণ তোমার নিজের অ্যাকাউন্ট, নিজের ঝুঁকি — Vyro-র ToS একবার পড়ে নেওয়া ভালো।
- `vyro_client.py`-এর selector গুলো Vyro তাদের ওয়েবসাইট রিডিজাইন করলে ভেঙে যেতে পারে — তখন আবার ধাপ ০ রিপিট করতে হবে।
- সোর্স ক্লিপ ডাউনলোড লজিক (`daily_runner.py`) yt-dlp দিয়ে চেষ্টা করে, না হলে সরাসরি HTTP download — Vyro যদি অন্য কোনো পদ্ধতিতে ক্লিপ দেয় (যেমন লগইন-প্রোটেক্টেড লিংক), সেটার জন্য আলাদা হ্যান্ডলিং লাগবে।

## নোট
- `push` ইভেন্টে পুরনো `vyro_pipeline.yml` অটো-ট্রিগার হয় শুধু তখনই যখন `campaign.json` মডিফাই হয়ে মেইন ব্রাঞ্চে পুশ হয় — এটা ম্যানুয়াল/টেস্ট রানের জন্য রাখা হয়েছে।
- CI রানারে `TextClip`-এর জন্য ImageMagick policy issue হতে পারে; হলে `/etc/ImageMagick-6/policy.xml`-এ `PDF`/`TEXT` rights সংশোধন লাগতে পারে।
- OAuth রিফ্রেশ টোকেন সাধারণত মেয়াদ শেষ হয় না যতক্ষণ না আপনি নিজে Google অ্যাকাউন্ট থেকে অ্যাক্সেস revoke করেন; করলে ধাপ ১ আবার করতে হবে।
