# Cloud Infrastructure Context - Azano

## VPS (Oracle Cloud - Always Free)
- 1 OCPU / 6GB RAM (Ampere A1)
- ~46GB storage
- Public IPv4 enabled
- OS: Oracle Linux (dnf)
- Used for persistent or heavy processing workloads

## Networking & Security
- Cloudflare used as reverse proxy ONLY for VPS services
- Public IP restricted via NSG rules
- Only Cloudflare IPv4 ranges allowed
- Direct IP access blocked
- Bot Fight Mode enabled (with caution)

## Architecture Preference
- Frontend: Vercel (Astro / React / Next.js)
- APIs: Serverless (Vercel Functions)
- Critical APIs: VPS + Cloudflare
- Hybrid architecture preferred

## Security Strategy
- Vercel: platform protection + code validation
- VPS: Cloudflare + firewall
- Never trust frontend
- Rate limiting via code or external services (Upstash)

## Limitations
- Do NOT use Cloudflare proxy in front of Vercel
- Free tier VPS has limited CPU
- No advanced WAF (free tier)

## Philosophy
- Keep systems simple
- Prefer serverless
- Use VPS only when necessary
- Avoid unnecessary complexity
