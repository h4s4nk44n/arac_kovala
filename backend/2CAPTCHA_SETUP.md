# 2Captcha Setup Guide

## Why 2Captcha?

The previous Buster Chrome extension approach failed because:
- Buster's solver button is in a closed shadow root (can't be detected via CSS)
- Coordinate-based clicking is unreliable across different screen sizes
- Extension-based solving is slow and has low success rates

**2Captcha is a professional CAPTCHA solving service** with:
- ✅ 95%+ success rate for reCAPTCHA v2/v3
- ✅ Fast solving (30-60 seconds average)
- ✅ API-based (no browser extension needed)
- ✅ Very affordable ($2.99 per 1000 CAPTCHAs)

## Setup Steps

### 1. Create 2Captcha Account

1. Go to [https://2captcha.com](https://2captcha.com)
2. Click "Sign Up" and create an account
3. Verify your email address

### 2. Get API Key

1. Log in to your 2Captcha dashboard
2. Go to **Settings** → **API Key**
3. Copy your API key (looks like: `a1b2c3d4e5f6g7h8i9j0k1l2m3n4o5p6`)

### 3. Add Funds

1. Go to **Balance** → **Add Funds**
2. Minimum deposit: $3 (solves ~1000 CAPTCHAs)
3. Payment methods: PayPal, Bitcoin, cards, etc.

### 4. Set Environment Variable in Railway

1. Go to your Railway project
2. Click on **Variables** tab
3. Add new variable:
   - **Name**: `TWOCAPTCHA_API_KEY`
   - **Value**: `your_api_key_here` (paste from step 2)
4. Click **Save**

### 5. Deploy and Test

```bash
git add backend/app.py backend/2CAPTCHA_SETUP.md
git commit -m "Integrate 2Captcha API for reliable CAPTCHA solving"
git push
```

Watch Railway logs for:
```
[2Captcha] ✓ Found sitekey: 6LfD3PIbAAAAAJs_eEH...
[2Captcha] ✓ CAPTCHA submitted, ID: 72648362947
[2Captcha] Waiting for solution (this may take 30-60 seconds)...
[2Captcha] ✓ Solution received after 42s
[2Captcha] ✓ Solution injected successfully
[Login] ✓ 2Captcha solved the CAPTCHA!
✓ Login appears successful
```

## Cost Estimate

- **Price**: $2.99 per 1000 reCAPTCHA solves
- **Your usage**: 
  - 1 login attempt = 1-2 CAPTCHAs
  - 3 retries = max 6 CAPTCHAs per login attempt
  - If you login once per day = ~$0.06/month
  - If you login 10 times per day = ~$0.60/month

**Very affordable!** You can start with $3 and it will last months.

## Troubleshooting

### "TWOCAPTCHA_API_KEY not set"
- Make sure you added the environment variable in Railway
- Restart the deployment after adding the variable

### "Submit failed: ERROR_ZERO_BALANCE"
- Your 2Captcha account balance is $0
- Add funds: https://2captcha.com/enterpage

### "Submit failed: ERROR_WRONG_USER_KEY"
- Your API key is incorrect
- Double-check the key from: https://2captcha.com/setting

### "Timeout after 120s"
- 2Captcha servers might be slow
- Check your balance: https://2captcha.com/statistics
- Increase timeout in code if needed (default: 120s)

## Alternative Services

If 2Captcha doesn't work, you can also use:

1. **Anti-Captcha** (https://anti-captcha.com)
   - Similar pricing (~$2/1000 solves)
   - Slightly faster but less reliable

2. **CapSolver** (https://www.capsolver.com)
   - Cheaper ($0.8/1000 solves)
   - Newer service, less proven

To switch, just modify `_solve_recaptcha_with_2captcha()` function in `app.py` to use their API endpoints.

## Support

- 2Captcha docs: https://2captcha.com/2captcha-api
- 2Captcha support: https://2captcha.com/support
- API status: https://2captcha.com/api-ratelimit
