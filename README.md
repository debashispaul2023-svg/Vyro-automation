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

## ⚠️ ধাপ ০ (বাধ্যতামূলক): Vyro সিলেক্টর বের করা

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
2. GitHub রিপোর **Settings → Secrets and variables → Actions**-এ এই ৩টা secret যোগ করো:
   - `VYRO_EMAIL` — তোমার Vyro লগইন ইমেইল
   - `VYRO_PASSWORD` — তোমার Vyro পাসওয়ার্ড
   - `YOUTUBE_TOKEN_JSON` — `token.json` ফাইলের পুরো কন্টেন্ট (রেখে দিন, base64 লাগবে না — সরাসরি JSON content)
3. `.github/workflows/vyro_daily.yml` ফাইলে `cron: "0 6 * * *"` — চাইলে সময় বদলাও ([crontab.guru](https://crontab.guru) দিয়ে সহজে বানানো যায়)
4. রিপোতে সব ফাইল পুশ করো (`token.json`/`client_secrets.json` ছাড়া)
5. **Actions → Vyro Daily Full Automation → Run workflow** দিয়ে প্রথমবার ম্যানুয়ালি টেস্ট করো
6. Job লগ দেখো — কোনো selector fail করলে ঠিক কোন লাইনে সমস্যা সেটা এরর মেসেজে বলে দেবে

এরপর থেকে প্রতিদিন নিজে থেকে চলবে: ক্যাম্পেইন না থাকলে চুপচাপ স্কিপ করবে,
থাকলে পুরো পাইপলাইন চালিয়ে Vyro-তে লিংক জমা দিয়ে দেবে, এবং
`processed_campaigns.json`-এ লিখে রাখবে যাতে একই ক্যাম্পেইন দুইবার সাবমিট না হয়।

## গুরুত্বপূর্ণ সতর্কতা
- Vyro-তে bot দিয়ে auto-login/auto-submit করা তাদের Terms of Service ভঙ্গ করতে পারে। এটা সম্পূর্ণ তোমার নিজের অ্যাকাউন্ট, নিজের ঝুঁকি — Vyro-র ToS একবার পড়ে নেওয়া ভালো।
- `vyro_client.py`-এর selector গুলো Vyro তাদের ওয়েবসাইট রিডিজাইন করলে ভেঙে যেতে পারে — তখন আবার ধাপ ০ রিপিট করতে হবে।
- সোর্স ক্লিপ ডাউনলোড লজিক (`daily_runner.py`) yt-dlp দিয়ে চেষ্টা করে, না হলে সরাসরি HTTP download — Vyro যদি অন্য কোনো পদ্ধতিতে ক্লিপ দেয় (যেমন লগইন-প্রোটেক্টেড লিংক), সেটার জন্য আলাদা হ্যান্ডলিং লাগবে।

## নোট
- `push` ইভেন্টে পুরনো `vyro_pipeline.yml` অটো-ট্রিগার হয় শুধু তখনই যখন `campaign.json` মডিফাই হয়ে মেইন ব্রাঞ্চে পুশ হয় — এটা ম্যানুয়াল/টেস্ট রানের জন্য রাখা হয়েছে।
- CI রানারে `TextClip`-এর জন্য ImageMagick policy issue হতে পারে; হলে `/etc/ImageMagick-6/policy.xml`-এ `PDF`/`TEXT` rights সংশোধন লাগতে পারে।
- OAuth রিফ্রেশ টোকেন সাধারণত মেয়াদ শেষ হয় না যতক্ষণ না আপনি নিজে Google অ্যাকাউন্ট থেকে অ্যাক্সেস revoke করেন; করলে ধাপ ১ আবার করতে হবে।
