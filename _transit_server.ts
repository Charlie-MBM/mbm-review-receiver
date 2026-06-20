// perf: bump 2026-05-19 — force worker redeploy to ship PR #7 SEO meta (canonical + og:* + twitter:*) on routes whose bundles were cached over the original deploy
// perf: bump 2026-05-13 — force worker redeploy
// perf: bump 2026-05-13 — force worker redeploy to pick up applyCacheHeaders override
// perf: bump 2026-05-13b — iter18, force worker redeploy
import "./lib/error-capture";

import { consumeLastCapturedError } from "./lib/error-capture";
import { renderErrorPage } from "./lib/error-page";

type ServerEntry = {
  fetch: (request: Request, env: unknown, ctx: unknown) => Promise<Response> | Response;
};

let serverEntryPromise: Promise<ServerEntry> | undefined;

async function getServerEntry(): Promise<ServerEntry> {
  if (!serverEntryPromise) {
    serverEntryPromise = import("@tanstack/react-start/server-entry").then(
      (m) => ((m as { default?: ServerEntry }).default ?? (m as unknown as ServerEntry)),
    );
  }
  return serverEntryPromise;
}

function brandedErrorResponse(): Response {
  return new Response(renderErrorPage(), {
    status: 500,
    headers: { "content-type": "text/html; charset=utf-8" },
  });
}

function isCatastrophicSsrErrorBody(body: string, responseStatus: number): boolean {
  let payload: unknown;
  try {
    payload = JSON.parse(body);
  } catch {
    return false;
  }

  if (!payload || Array.isArray(payload) || typeof payload !== "object") {
    return false;
  }

  const fields = payload as Record<string, unknown>;
  const expectedKeys = new Set(["message", "status", "unhandled"]);
  if (!Object.keys(fields).every((key) => expectedKeys.has(key))) {
    return false;
  }

  return (
    fields.unhandled === true &&
    fields.message === "HTTPError" &&
    (fields.status === undefined || fields.status === responseStatus)
  );
}

// h3 swallows in-handler throws into a normal 500 Response with body
// {"unhandled":true,"message":"HTTPError"} — try/catch alone never fires for those.
async function normalizeCatastrophicSsrResponse(response: Response): Promise<Response> {
  if (response.status < 500) return response;
  const contentType = response.headers.get("content-type") ?? "";
  if (!contentType.includes("application/json")) return response;

  const body = await response.clone().text();
  if (!isCatastrophicSsrErrorBody(body, response.status)) {
    return response;
  }

  console.error(consumeLastCapturedError() ?? new Error(`h3 swallowed SSR error: ${body}`));
  return brandedErrorResponse();
}

// TanStack Router renders the root notFoundComponent for unmatched routes but
// leaves the response status at 200 (soft-404). The NotFoundComponent embeds a
// hidden <meta data-mbm-status="404"> marker; rewrite the status here so
// crawlers see a real 404 while users still get the React-rendered page.
async function rewriteSoftNotFound(response: Response): Promise<Response> {
  if (response.status !== 200) return response;
  const contentType = response.headers.get("content-type") ?? "";
  if (!contentType.includes("text/html")) return response;

  const body = await response.clone().text();
  if (!body.includes('data-mbm-status="404"')) return response;

  const headers = new Headers(response.headers);
  return new Response(body, { status: 404, statusText: "Not Found", headers });
}

// Set Cache-Control on HTML route responses (hashed /assets/* keep their own
// long-lived immutable headers). Skips assets, leaves any existing
// Cache-Control header untouched. Also leaves Content-Encoding alone so the
// Cloudflare edge can apply Brotli/gzip based on Accept-Encoding.
function applyCacheHeaders(request: Request, response: Response): Response {
  const url = new URL(request.url);
  const path = url.pathname;

  // Hashed static assets — handled upstream; never override.
  if (path.startsWith("/assets/") || path.startsWith("/_build/")) return response;

  let cacheControl: string | null = null;

  if (path.startsWith("/fonts/")) {
    // Self-hosted woff2 fonts — long-cache per Lighthouse cache-insight
    cacheControl = "public, max-age=31536000, immutable";
  } else if (response.status === 404) {
    cacheControl = "no-store";
  } else if (path === "/sitemap.xml") {
    cacheControl = "public, max-age=3600";
  } else if (response.status >= 200 && response.status < 300) {
    const contentType = response.headers.get("content-type") ?? "";
    if (contentType.includes("text/html")) {
      cacheControl = "public, max-age=3600, s-maxage=86400, stale-while-revalidate=604800";
    }
  }

  // No rule applies — leave any existing Cache-Control alone.
  if (!cacheControl) return response;

  // Rule applies — overwrite any existing Cache-Control (TanStack Start's
  // default SSR sets "no-cache, must-revalidate, max-age=0" on HTML which
  // we explicitly want to replace).
  const headers = new Headers(response.headers);
  headers.set("cache-control", cacheControl);
  return new Response(response.body, {
    status: response.status,
    statusText: response.statusText,
    headers,
  });
}

/**
 * Migration redirects from legacy WordPress URLs to canonical Cloudflare Worker URLs.
 *
 * Source of truth: project/REDIRECT_PLAN.md Section 10 (25 entries from cannibalization
 * consolidation, blog migration, and WordPress structural cleanup) +
 * project/REQUIRES_HUMAN_INPUT.md item #4 (4 service-slug fallbacks for routes that
 * were renamed but where the old slug still has external backlinks).
 *
 * Total: 29 entries. All 301 permanent. Query strings preserved.
 *
 * Why these live in the Worker rather than Cloudflare Bulk Redirects: the
 * custom_domain: true route attachment makes the Worker the origin server for
 * the entire mtbakermedical.com hostname, which bypasses account-level Bulk
 * Redirects in practice. Handling them here is reliable and the CPU cost
 * (a single object property lookup per request) is negligible.
 */
const MIGRATION_REDIRECTS: Record<string, string> = {
  // Pre-existing Vercel redirects (kept verbatim per REDIRECT_PLAN Section 1)
  "/primary-care/": "/concierge-primary-care/",
  "/service/glp-1-weight-loss-therapy-bellingham/": "/services/glp-1-weight-loss-therapy/",

  // Ketamine cluster consolidation (REDIRECT_PLAN Section 2)
  "/ketamine-therapy-in-bellingham/": "/services/ketamine-therapy/",
  "/ketamine-therapy-a-new-approach/": "/services/ketamine-therapy/",
  "/ketamine-therapy-insurance/": "/services/ketamine-therapy/",
  "/ketamine-therapy-in-bellingham-wa-treatment-for-depression-anxiety-ptsd-mt-baker-medical/": "/services/ketamine-therapy/",
  "/understanding-treatment-resistant-depression/": "/services/ketamine-therapy/",
  "/how-ketamine-therapy-works-the-science-behind-rapid-depression-relief/": "/services/ketamine-therapy/",
  "/ketamine-therapy-a-new-option-for-treatment-resistant-depression/": "/services/ketamine-therapy/",

  // GLP-1 cluster consolidation (REDIRECT_PLAN Section 3)
  "/the-health-benefits-of-glp-1-therapy-a-physician-guided-approach-to-sustainable-weight-loss/": "/services/glp-1-weight-loss-therapy/",
  "/could-glp-1s-be-the-next-breakthrough-in-addiction-treatment/": "/services/glp-1-weight-loss-therapy/",
  "/glp-1-agonists-may-significantly-improve-survival-in-colon-cancer-patients-study-finds/": "/services/glp-1-weight-loss-therapy/",

  // Blog rewrites (REDIRECT_PLAN Section 4 — OPTION 2)
  "/why-metabolic-health-is-the-foundation-of-longevity-and-how-to-improve-it-in-2026/": "/blog/why-metabolic-health-is-the-foundation-of-longevity/",
  "/anxiety-vs-depression-how-to-tell-the-difference/": "/blog/anxiety-vs-depression-how-to-tell-the-difference/",

  // Blog retires (REDIRECT_PLAN Section 5 — OPTION 4)
  "/making-lasting-health-changes-in-2026-how-mt-baker-medical-helps-you-reach-your-goals/": "/concierge-primary-care/",
  "/mt-baker-medical-is-opening-soon/": "/",

  // WordPress structural cleanup (REDIRECT_PLAN Section 6)
  "/service/": "/services/",
  "/category/treatments/": "/services/",
  "/category/treatments/ketamine/": "/services/ketamine-therapy/",
  "/category/treatments/glp-1/": "/services/glp-1-weight-loss-therapy/",
  "/category/conditions/depression/": "/conditions/depression/",
  "/category/conditions/anxiety/": "/conditions/anxiety/",
  "/category/conditions/weightloss/": "/services/glp-1-weight-loss-therapy/",
  "/author/mtbakermed/": "/about/",

  // Defensive cleanup (REDIRECT_PLAN Section 7)
  "/coming-soon/": "/",

  // Service-slug fallbacks (REQUIRES_HUMAN_INPUT.md item #4)
  // These cover the case where the renamed route hasn't been redirected from old slug.
  "/services/hormone-replacement": "/services/hormone-replacement-therapy/",
  "/services/glp-1-weight-loss": "/services/glp-1-weight-loss-therapy/",
  "/services/testosterone-replacement": "/services/testosterone-replacement-therapy/",
  "/services/custom-supplements": "/services/custom-supplements-and-nutraceutical/",

  // GSC-discovered gaps (post-launch)
  "/concierge-medicine-bellingham-wa/": "/concierge-primary-care/",
  "/services/renasculpt-muscle-therapy/": "/services/",
  // Peptide therapy retirement (2026-05-22) — service decommissioned for compliance
  "/services/peptide-therapy/": "/services/",
  "/concierge": "/concierge-primary-care/",

  // GSC-discovered gaps round 2 (2026-05-19)
  // Trailing-slash variant of /concierge (no-slash variant already covered above)
  "/concierge/": "/concierge-primary-care/",
  // Semaglutide-vs-tirzepatide blog post existed pre-rebuild; consolidate to the canonical service page
  "/semaglutide-vs-tirzepatide-glp1-weight-loss/": "/services/glp-1-weight-loss-therapy/",
  // Service-slug shorthand variants found in GSC Crawled-Not-Indexed
  "/services/weight-loss-therapy/": "/services/glp-1-weight-loss-therapy/",
  "/services/weightloss-therapy/": "/services/glp-1-weight-loss-therapy/",
  // Bare /category/conditions/ archive (WP relic; sub-paths like /anxiety/ already covered)
  "/category/conditions/": "/conditions/",
};

export default {
  async fetch(request: Request, env: unknown, ctx: unknown) {
    const url = new URL(request.url);

    // ─── Click-tracker for /review page Google CTA ──────────────────────────
    //
    // The /review landing page fires a fire-and-forget POST here when a patient
    // clicks "Share your experience on Google" or "Write a Google review". The
    // daily poller GETs the list before sending review-request SMSes and skips
    // patients whose first name has already clicked — so we never pester someone
    // who's already engaged with the ask.
    //
    // HIPAA: only {first_name, first_clicked_at} stored. No phone, no email, no
    // patient_id. First name alone isn't a HIPAA identifier; combined with the
    // care relationship inferred by the SMS-origin click it's borderline, but the
    // data on Cloudflare KV is intentionally minimal. Cloudflare is NOT
    // BAA-covered, so this is the most we put there.
    //
    // Storage: Cloudflare KV namespace REVIEW_CLICKS (bound in wrangler.jsonc).
    // GET is gated by CLICK_TRACKER_TOKEN (env var) so only the poller can read.
    // POST is open (anyone can claim they clicked — worst case a few false
    // positives, which only suppress sends, never trigger them).
    if (url.pathname === "/api/review-clicked") {
      const kv = (env as { REVIEW_CLICKS?: any })?.REVIEW_CLICKS;

      // If no KV binding is attached (i.e., we're running on Lovable's hosting
      // runtime at 185.158.133.1, which doesn't expose Cloudflare bindings),
      // forward the request to the dedicated Cloudflare Worker at
      // mtbakermedical.charlie-6c7.workers.dev which DOES have the bindings.
      // The Worker handles the actual KV write/read; we just proxy.
      // See PHASE5_STATUS.md for the architectural discovery.
      if (!kv) {
        const forwardUrl = "https://mtbakermedical.charlie-6c7.workers.dev/api/review-clicked";
        const fwdHeaders = new Headers(request.headers);
        fwdHeaders.delete("host");
        fwdHeaders.delete("x-forwarded-host");
        const init: RequestInit = { method: request.method, headers: fwdHeaders };
        if (request.method === "POST") {
          init.body = await request.text();
        }
        return fetch(forwardUrl, init);
      }

      // Cloudflare Worker path — KV binding present, handle locally.
      // Click-tracker stores SHA-256 hashes of first names, NOT the names
      // themselves, so no consumer health data lives on Cloudflare KV.
      // Cloudflare isn't BAA-covered with the practice, and WA MHMD treats
      // "data indicating a consumer used healthcare-related services" as
      // protected. Hashing preserves the suppression behavior while
      // eliminating PHI / consumer-health-data exposure at the storage layer.
      if (request.method === "POST") {
        let body: unknown;
        try {
          body = await request.json();
        } catch {
          return new Response(JSON.stringify({ error: "bad json" }), {
            status: 400,
            headers: { "content-type": "application/json" },
          });
        }
        const rawName = (body as { fname?: unknown })?.fname ?? "";
        const fname = String(rawName).trim().toLowerCase().slice(0, 40);
        if (!fname) {
          return new Response(JSON.stringify({ error: "missing fname" }), {
            status: 400,
            headers: { "content-type": "application/json" },
          });
        }
        // Hash the first name with SHA-256 before storing. Plain first name
        // never lands in KV.
        const encoded = new TextEncoder().encode(fname);
        const hashBuf = await crypto.subtle.digest("SHA-256", encoded);
        const fnameHash = Array.from(new Uint8Array(hashBuf))
          .map((b) => b.toString(16).padStart(2, "0"))
          .join("");
        const ts = new Date().toISOString();
        // putIfAbsent semantics: only set on first click; subsequent clicks
        // keep the earliest timestamp.
        const existing = await kv.get(`fname:${fnameHash}`);
        if (!existing) {
          await kv.put(`fname:${fnameHash}`, ts);
        }
        return new Response(
          JSON.stringify({ ok: true, first_clicked_at: existing || ts }),
          { status: 200, headers: { "content-type": "application/json" } },
        );
      }

      if (request.method === "GET") {
        const auth = request.headers.get("authorization") || "";
        const expected = (env as { CLICK_TRACKER_TOKEN?: string })?.CLICK_TRACKER_TOKEN || "";
        if (!expected || auth !== `Bearer ${expected}`) {
          return new Response(JSON.stringify({ error: "unauthorized" }), {
            status: 401,
            headers: { "content-type": "application/json" },
          });
        }
        const list = await kv.list({ prefix: "fname:" });
        const records = await Promise.all(
          list.keys.map(async (k: { name: string }) => ({
            fname: k.name.replace("fname:", ""),
            first_clicked_at: await kv.get(k.name),
          })),
        );
        return new Response(JSON.stringify({ records }), {
          status: 200,
          headers: { "content-type": "application/json" },
        });
      }

      return new Response(JSON.stringify({ error: "method not allowed" }), {
        status: 405,
        headers: { "content-type": "application/json" },
      });
    }

    // Short URL for SMS review requests — /r?n=Charles → /review?fname=Charles
    // Saves ~12 characters in the SMS body. Constructed by the poller in
    // mbm-review-receiver/hint_webhook_receiver.py (see send_review_sms).
    if (url.pathname === "/r") {
      const n = url.searchParams.get("n");
      const destination = new URL("/review", url.origin);
      if (n) destination.searchParams.set("fname", n);
      return Response.redirect(destination.toString(), 302);
    }

    // Migration redirect check — handle legacy WordPress URLs before any other
    // logic. Exact pathname match; query string preserved on the destination.
    const redirectTarget = MIGRATION_REDIRECTS[url.pathname];
    if (redirectTarget) {
      const destination = new URL(redirectTarget, url.origin);
      destination.search = url.search;
      return Response.redirect(destination.toString(), 301);
    }

    try {
      const handler = await getServerEntry();
      const response = await handler.fetch(request, env, ctx);
      const normalized = await normalizeCatastrophicSsrResponse(response);
      const notFoundFixed = await rewriteSoftNotFound(normalized);
      return applyCacheHeaders(request, notFoundFixed);
    } catch (error) {
      console.error(error);
      return brandedErrorResponse();
    }
  },
};
