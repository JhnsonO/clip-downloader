# Deploying Clip Downloader to Railway

Five minutes, free tier, no credit card needed.

---

## 1. Put the code on GitHub

1. Go to https://github.com/new and create a **private** repository called `clip-downloader`
2. On your computer, open Terminal (Mac) or Command Prompt (Windows) in this folder and run:

```
git init
git add .
git commit -m "initial"
git remote add origin https://github.com/YOUR_USERNAME/clip-downloader.git
git push -u origin main
```

---

## 2. Deploy on Railway

1. Go to https://railway.app and sign in with GitHub
2. Click **"New Project"** → **"Deploy from GitHub repo"**
3. Select your `clip-downloader` repo
4. Railway detects the Dockerfile automatically and starts building (~2 min)
5. Once deployed, click your project → **"Settings"** → **"Generate Domain"**
6. You'll get a URL like `https://clip-downloader-production.up.railway.app`

That's it. Share that URL with Kris and Wade.

---

## Free tier limits

Railway's free tier gives you **$5 of credit/month** which is plenty for light internal use
(roughly 500 hours of runtime). The app sleeps when idle and wakes on first request.

If you exceed it, the Hobby plan is $5/month.

---

## Updating the app

Any push to your GitHub repo auto-redeploys:

```
git add .
git commit -m "update"
git push
```

---

## Notes

- Downloads are processed on the server and streamed to the device
- Files are deleted from the server ~30 seconds after download
- The app is private (no auth) — only share the URL with people you trust
- If you want a password, add `SECRET_KEY=yourpassword` as a Railway env variable
  and let me know — I can add a simple PIN screen
