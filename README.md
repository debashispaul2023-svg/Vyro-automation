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
├── main.py
├── requirements.txt
├── campaign.json
└── .github/workflows/vyro_pipeline.yml
```

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

## নোট
- `push` ইভেন্টে workflow অটো-ট্রিগার হয় শুধু তখনই যখন `campaign.json` মডিফাই হয়ে মেইন ব্রাঞ্চে পুশ হয়।
- CI রানারে `TextClip`-এর জন্য ImageMagick policy issue হতে পারে; হলে `/etc/ImageMagick-6/policy.xml`-এ `PDF`/`TEXT` rights সংশোধন লাগতে পারে।
- OAuth রিফ্রেশ টোকেন সাধারণত মেয়াদ শেষ হয় না যতক্ষণ না আপনি নিজে Google অ্যাকাউন্ট থেকে অ্যাক্সেস revoke করেন; করলে ধাপ ১ আবার করতে হবে।
