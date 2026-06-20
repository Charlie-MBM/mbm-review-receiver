import { createFileRoute } from "@tanstack/react-router";
import { useEffect, useState } from "react";

const SITE_ORIGIN = "https://mtbakermedical.com";
const PAGE_URL = `${SITE_ORIGIN}/review/`;

const META_TITLE = "Share Your Experience | Mt. Baker Medical, Bellingham WA";
const META_DESCRIPTION =
  "Share your experience with Mt. Baker Medical. Your review helps another patient in Bellingham find concierge primary care that actually listens.";

const GOOGLE_REVIEW_URL =
  "https://search.google.com/local/writereview?placeid=ChIJm8NquAOjhVQRCanaH1z4viE";
const FEEDBACK_EMAIL = "care@mtbakermedical.com";

const WEBPAGE_SCHEMA = {
  "@context": "https://schema.org",
  "@type": "WebPage",
  "@id": `${PAGE_URL}#webpage`,
  url: PAGE_URL,
  name: META_TITLE,
  description: META_DESCRIPTION,
  inLanguage: "en-US",
  isPartOf: { "@id": `${SITE_ORIGIN}/#website` },
};

function sanitizeFname(raw: unknown): string | undefined {
  if (typeof raw !== "string") return undefined;
  const cleaned = raw.replace(/[^\p{L}\p{M}\s'.\-]/gu, "").trim().slice(0, 40);
  return cleaned.length > 0 ? cleaned : undefined;
}

export const Route = createFileRoute("/review")({
  validateSearch: (search: Record<string, unknown>) => ({
    fname: sanitizeFname(search.fname),
  }),
  head: () => ({
    meta: [
      { title: META_TITLE },
      { name: "description", content: META_DESCRIPTION },
      { name: "robots", content: "noindex, nofollow" },
    ],
    links: [{ rel: "canonical", href: PAGE_URL }],
    scripts: [
      { type: "application/ld+json", children: JSON.stringify(WEBPAGE_SCHEMA) },
    ],
  }),
  component: ReviewPage,
});

const eyebrow: React.CSSProperties = {
  fontFamily: "Inter, sans-serif",
  fontSize: 12,
  fontWeight: 600,
  textTransform: "uppercase",
  letterSpacing: "0.18em",
  color: "var(--gold-deep)",
  display: "block",
  marginBottom: 24,
};

const primaryGoldBtn: React.CSSProperties = {
  background: "var(--gold-deep)",
  color: "var(--cream)",
  padding: "16px 28px",
  borderRadius: 9999,
  fontFamily: "Inter, sans-serif",
  fontSize: 16,
  fontWeight: 600,
  textDecoration: "none",
  display: "inline-flex",
  alignItems: "center",
  justifyContent: "center",
  minHeight: 52,
  lineHeight: 1.2,
  boxShadow: "0 6px 18px rgba(201, 149, 12, 0.28)",
};

function ReviewPage() {
  // Capture fname on first render via useState. Without this, the URL-strip
  // useEffect below mutates the URL via history.replaceState which causes
  // TanStack Router's useSearch() to re-run with fname now undefined,
  // triggering a re-render that drops the personalization AND breaks the
  // click-tracker (handleGoogleClick gates on fname being truthy).
  const _urlFname = Route.useSearch().fname;
  const [fname] = useState(_urlFname);
  const greeting = fname
    ? `Tell the next person, ${fname}.`
    : "Tell the next person.";

  // Fire-and-forget click signal for either Google CTA. Only meaningful when
  // we know who the patient is (fname present). Never blocks navigation.
  const handleGoogleClick = () => {
    if (!fname) return;
    try {
      fetch("/api/review-clicked", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ fname }),
        keepalive: true,
      }).catch(() => {});
    } catch {
      // Swallow — click should never fail to navigate just because tracking did.
    }
  };

  // HIPAA / WA MHMD: strip ?fname= from the URL after read so the patient's first
  // name doesn't leak into referrer headers on outbound clicks, Cloudflare access
  // logs, browser history, or analytics. Personalization in the rendered page is
  // preserved (already captured in `fname` above); only the URL is cleaned.
  useEffect(() => {
    if (typeof window === "undefined") return;
    if (!window.location.search.includes("fname=")) return;
    const url = new URL(window.location.href);
    url.searchParams.delete("fname");
    const cleaned = url.pathname + (url.search ? url.search : "") + url.hash;
    window.history.replaceState({}, "", cleaned);
  }, []);


  return (
    <main style={{ background: "var(--cream)", color: "var(--ink)" }}>
      {/* Utility bar removed on /review */}


      {/* ── 1. HERO — restructured with above-fold CTA ──────── */}
      <section
        className="review-hero"
        style={{
          background:
            "linear-gradient(180deg, var(--pq-mint) 0%, var(--pq-lavender) 100%)",
          padding: "72px 0 60px",
        }}
      >
        <div
          style={{
            maxWidth: 820,
            margin: "0 auto",
            padding: "0 24px",
            textAlign: "center",
          }}
        >
          <span style={eyebrow}>A NOTE OF GRATITUDE</span>
          <h1
            style={{
              fontFamily: "'Cormorant Garamond', Georgia, serif",
              fontSize: "clamp(44px, 8vw, 96px)",
              lineHeight: 0.98,
              fontWeight: 500,
              color: "var(--ink)",
              margin: "0 0 20px 0",
              letterSpacing: "-0.02em",
              overflowWrap: "break-word",
            }}
          >
            {greeting}
          </h1>
          <p
            style={{
              fontFamily: "'Cormorant Garamond', Georgia, serif",
              fontStyle: "italic",
              fontSize: "clamp(22px, 2.6vw, 32px)",
              fontWeight: 500,
              color: "var(--gold-deep)",
              margin: "0 auto 32px",
              maxWidth: 660,
              lineHeight: 1.3,
            }}
          >
            We get to practice medicine the slow way — listening, taking time, actually finding what's wrong — because we sit outside the insurance system. The only way another patient ever finds care like this is when someone like you says it was worth it.
          </p>

          {/* Primary above-fold CTA */}
          <a
            href={GOOGLE_REVIEW_URL}
            target="_blank"
            rel="noopener noreferrer"
            onClick={handleGoogleClick}
            className="review-hero-cta"
            style={{
              ...primaryGoldBtn,
              width: "100%",
              maxWidth: 380,
            }}
          >
            Share your experience on Google →
          </a>

          {/* Secondary link */}
          <div style={{ marginTop: 16 }}>
            <a
              href="#private"
              style={{
                fontFamily: "Inter, sans-serif",
                fontSize: 13,
                color: "var(--ink-2)",
                textDecoration: "underline",
                textUnderlineOffset: 3,
              }}
            >
              Or send Dr. Scribner private feedback →
            </a>
          </div>
        </div>
      </section>

      {/* ── 2. A NOTE FROM DR. SCRIBNER ───────────────────── */}
      <section style={{ background: "var(--cream)", padding: "90px 0 60px" }}>
        <div style={{ maxWidth: 720, margin: "0 auto", padding: "0 24px" }}>
          <div
            style={{
              background: "var(--cream-paper)",
              borderLeft: "4px solid var(--gold-deep)",
              padding: "40px 36px 36px",
              borderRadius: "0 4px 4px 0",
            }}
          >
            <span
              style={{
                fontFamily: "Inter, sans-serif",
                fontSize: 11,
                fontWeight: 600,
                textTransform: "uppercase",
                letterSpacing: "0.18em",
                color: "var(--gold-deep)",
                display: "block",
                marginBottom: 20,
              }}
            >
              A NOTE FROM DR. SCRIBNER
            </span>
            <div
              style={{
                fontFamily: "'Cormorant Garamond', Georgia, serif",
                fontSize: 23,
                lineHeight: 1.55,
                color: "var(--ink)",
                fontStyle: "italic",
                fontWeight: 400,
              }}
            >
              <p style={{ margin: "0 0 18px 0" }}>
                I built this practice because I wanted to do the kind of
                medicine I’d want for my own family — the kind where someone
                actually listened, had time, actually wanted to find what was
                wrong instead of moving on to the next fifteen-minute slot.
              </p>
              <p style={{ margin: "0 0 18px 0" }}>
                That’s only possible because we don’t take insurance. We don’t
                have to bill in fifteen-minute chunks, don’t have to chase prior
                auths, don’t have to send you somewhere else when your problem
                doesn’t fit a billing code. We get to practice medicine the way
                it’s supposed to be practiced.
              </p>
              <p style={{ margin: "0 0 18px 0" }}>
                But that also means we don’t appear in any insurance directory.
                We don’t get matched with patients through a network. The next
                person in Bellingham who needs care like this — who’s been to
                three primary care offices that wouldn’t listen — will only
                find us if patients like you write down what your experience
                was actually like.
              </p>
              <p style={{ margin: 0 }}>
                Whatever you write, in your own words, is honestly how this kind
                of medicine survives outside the insurance system.
              </p>
            </div>
            <div
              style={{
                marginTop: 24,
                paddingTop: 18,
                borderTop: "1px solid var(--line)",
                fontFamily: "Inter, sans-serif",
                fontSize: 12,
                color: "var(--ink-3)",
                letterSpacing: "0.08em",
                textTransform: "uppercase",
              }}
            >
              — James Scribner, MD
            </div>
          </div>
        </div>
      </section>

      {/* ── 4. WHAT TO WRITE ────────────────────────────── */}
      <section style={{ background: "var(--cream)", padding: "40px 0 100px" }}>
        <div style={{ maxWidth: 760, margin: "0 auto", padding: "0 24px", textAlign: "center" }}>
          <span style={eyebrow}>NOT SURE WHAT TO WRITE?</span>
          <h2
            style={{
              fontFamily: "'Cormorant Garamond', Georgia, serif",
              fontSize: "clamp(28px, 3.4vw, 36px)",
              lineHeight: 1.15,
              fontWeight: 500,
              margin: "0 0 36px 0",
              letterSpacing: "-0.01em",
            }}
          >
            Some things patients often mention.
          </h2>

          <ul
            className="review-prompts-grid"
            style={{
              listStyle: "none",
              padding: 0,
              margin: "0 0 40px 0",
              display: "grid",
              gridTemplateColumns: "1fr 1fr",
              gap: "18px 32px",
              textAlign: "left",
            }}
          >
            {[
              "How long did you actually get to talk?",
              "What was different from other doctors you’ve seen?",
              "What surprised you?",
              "What might another patient need to know to decide?",
              "Why did you choose concierge care?",
              "Would you recommend this to a friend, and why?",
            ].map((q) => (
              <li
                key={q}
                style={{
                  fontFamily: "'Cormorant Garamond', Georgia, serif",
                  fontStyle: "italic",
                  fontSize: 21,
                  lineHeight: 1.4,
                  color: "var(--ink-2)",
                  paddingLeft: 18,
                  borderLeft: "2px solid var(--gold-deep)",
                }}
              >
                {q}
              </li>
            ))}
          </ul>

          <a
            href={GOOGLE_REVIEW_URL}
            target="_blank"
            rel="noopener noreferrer"
            style={{
              ...primaryGoldBtn,
              fontSize: 15,
              padding: "14px 26px",
              minHeight: 48,
            }}
          >
            Share on Google →
          </a>
        </div>
      </section>

      {/* ── 5. TWO-PATH CARDS — asymmetric ─────────────────── */}
      <section style={{ background: "var(--cream)", padding: "20px 0 120px" }}>
        <div style={{ maxWidth: 1100, margin: "0 auto", padding: "0 24px" }}>
          <div
            className="review-paths-grid"
            style={{
              display: "grid",
              gridTemplateColumns: "2fr 1fr",
              gap: 24,
              alignItems: "stretch",
            }}
          >
            {/* Primary — Google */}
            <div
              style={{
                background: "var(--green-deep)",
                color: "var(--cream)",
                border: "1px solid var(--green-deep)",
                borderRadius: 4,
                padding: "48px 40px",
                textAlign: "center",
                display: "flex",
                flexDirection: "column",
              }}
            >
              <div
                style={{
                  fontFamily: "'Cormorant Garamond', Georgia, serif",
                  fontStyle: "italic",
                  fontSize: 18,
                  color: "var(--sand)",
                  marginBottom: 14,
                }}
              >
                Public review
              </div>
              <h2
                style={{
                  fontFamily: "'Cormorant Garamond', Georgia, serif",
                  fontSize: "clamp(32px, 3.6vw, 42px)",
                  lineHeight: 1.1,
                  fontWeight: 500,
                  margin: "0 0 16px 0",
                  color: "var(--cream)",
                  letterSpacing: "-0.01em",
                }}
              >
                Share on Google
              </h2>
              <p
                style={{
                  fontFamily: "Inter, sans-serif",
                  fontSize: 17,
                  lineHeight: 1.65,
                  color: "rgba(251, 246, 236, 0.88)",
                  margin: "0 auto 28px",
                  maxWidth: 460,
                  flexGrow: 1,
                }}
              >
                A Google review is what shows up when someone in Bellingham
                searches for a doctor at midnight. It’s the single
                highest-leverage thing you can do for the next patient.
              </p>
              <a
                href={GOOGLE_REVIEW_URL}
                target="_blank"
                rel="noopener noreferrer"
                onClick={handleGoogleClick}
                style={{
                  ...primaryGoldBtn,
                  alignSelf: "center",
                }}
              >
                Write a Google review →
              </a>
            </div>

            {/* Secondary — Private feedback */}
            <div
              id="private"
              style={{
                background: "var(--cream-paper)",
                color: "var(--ink)",
                border: "1px solid var(--line)",
                borderRadius: 4,
                padding: "40px 28px",
                textAlign: "center",
                display: "flex",
                flexDirection: "column",
                scrollMarginTop: 24,
              }}
            >
              <div
                style={{
                  fontFamily: "'Cormorant Garamond', Georgia, serif",
                  fontStyle: "italic",
                  fontSize: 16,
                  color: "var(--gold-deep)",
                  marginBottom: 12,
                }}
              >
                Private feedback
              </div>
              <h2
                style={{
                  fontFamily: "'Cormorant Garamond', Georgia, serif",
                  fontSize: 26,
                  lineHeight: 1.15,
                  fontWeight: 500,
                  margin: "0 0 12px 0",
                  color: "var(--ink)",
                  letterSpacing: "-0.01em",
                }}
              >
                Tell us directly
              </h2>
              <p
                style={{
                  fontFamily: "Inter, sans-serif",
                  fontSize: 17,
                  lineHeight: 1.6,
                  color: "var(--ink-2)",
                  margin: "0 auto 20px",
                  flexGrow: 1,
                }}
              >
                If you’d rather speak with us directly, Dr. Scribner reads
                every note that comes through personally.
              </p>
              <PrivateFeedbackForm defaultName={fname ?? ""} />
              <p
                style={{
                  fontFamily: "Inter, sans-serif",
                  fontSize: 13,
                  color: "var(--ink-3)",
                  margin: "16px 0 0",
                }}
              >
                Or call us at{" "}
                <a
                  href="tel:+13604987529"
                  style={{ color: "var(--ink)", textDecoration: "underline", textUnderlineOffset: 3 }}
                >
                  (360) 498-7529
                </a>
                .
              </p>
            </div>
          </div>
        </div>
      </section>

      {/* ── 6. Other platforms ────────────────────────── */}
      <section style={{ background: "var(--cream-paper)", padding: "100px 0" }}>
        <div style={{ maxWidth: 1100, margin: "0 auto", padding: "0 48px" }}>
          <div style={{ textAlign: "center", maxWidth: 680, margin: "0 auto 48px" }}>
            <span style={eyebrow}>OR ELSEWHERE</span>
            <h2
              style={{
                fontFamily: "'Cormorant Garamond', Georgia, serif",
                fontSize: "clamp(28px, 3.4vw, 38px)",
                lineHeight: 1.1,
                fontWeight: 500,
                margin: "0 0 16px 0",
                letterSpacing: "-0.01em",
              }}
            >
              Other places the next patient looks.
            </h2>
            <p
              style={{
                fontFamily: "Inter, sans-serif",
                fontSize: 18,
                lineHeight: 1.65,
                color: "var(--ink-2)",
                margin: 0,
              }}
            >
              Different patients search different places. Whichever feels
              easiest helps the next one find us.
            </p>
          </div>

          <div
            className="review-platforms-grid"
            style={{
              display: "grid",
              gridTemplateColumns: "repeat(4, 1fr)",
              gap: 16,
              maxWidth: 920,
              margin: "0 auto",
            }}
          >
            <PlatformCard name="Healthgrades" href="https://www.healthgrades.com" />
            <PlatformCard name="Yelp" href="https://www.yelp.com" />
            <PlatformCard name="Vitals" href="https://www.vitals.com" />
            <PlatformCard name="Zocdoc" href="https://www.zocdoc.com" />
          </div>
        </div>
      </section>

      {/* ── 7. Heritage strip ───────────────────────── */}
      <section
        style={{
          background: "var(--green-deep)",
          color: "var(--cream)",
          padding: "90px 0",
          textAlign: "center",
        }}
      >
        <div style={{ maxWidth: 760, margin: "0 auto", padding: "0 48px" }}>
          <p
            style={{
              fontFamily: "'Cormorant Garamond', Georgia, serif",
              fontStyle: "italic",
              fontSize: "clamp(26px, 3.2vw, 36px)",
              lineHeight: 1.3,
              color: "var(--cream)",
              margin: "0 0 18px 0",
            }}
          >
            Care without the 5-minute clock.
          </p>
          <p
            style={{
              fontFamily: "Inter, sans-serif",
              fontSize: 14,
              lineHeight: 1.65,
              color: "rgba(232, 213, 183, 0.7)",
              letterSpacing: "0.04em",
              margin: 0,
            }}
          >
            Sycamore Square · 1200 Harris Ave Suite 308 · Bellingham, WA 98225
          </p>
        </div>
      </section>

      {/* ── 8. Footer ────────────────────────────────── */}
      <footer
        style={{
          background: "var(--green-deep)",
          color: "rgba(251, 246, 236, 0.5)",
          padding: "32px 0",
          borderTop: "1px solid var(--green-mid)",
          textAlign: "center",
          fontFamily: "Inter, sans-serif",
          fontSize: 12,
        }}
      >
        <div style={{ maxWidth: 1100, margin: "0 auto", padding: "0 48px" }}>
          © 2026 Scribner Medical Advisers, PLLC d/b/a Mt. Baker Medical ·{" "}
          <a
            href="/"
            style={{
              color: "rgba(251, 246, 236, 0.7)",
              textDecoration: "underline",
              textUnderlineOffset: 3,
            }}
          >
            mtbakermedical.com
          </a>
        </div>
      </footer>

      <style>{`
        @media (max-width: 900px) {
          .review-paths-grid { grid-template-columns: 1fr !important; }
          .review-platforms-grid { grid-template-columns: repeat(2, 1fr) !important; }
        }
        @media (max-width: 640px) {
          .review-prompts-grid { grid-template-columns: 1fr !important; }
        }
      `}</style>
    </main>
  );
}

function PlatformCard({ name, href }: { name: string; href: string }) {
  return (
    <a
      href={href}
      target="_blank"
      rel="noopener noreferrer"
      style={{
        background: "var(--cream)",
        border: "1px solid var(--line)",
        borderRadius: 4,
        padding: "24px 16px",
        textAlign: "center",
        textDecoration: "none",
        color: "var(--ink)",
        fontFamily: "'Cormorant Garamond', Georgia, serif",
        fontSize: 22,
        fontWeight: 500,
        minHeight: 64,
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        transition: "border-color 0.15s ease",
      }}
    >
      {name} →
    </a>
  );
}

function PrivateFeedbackForm({ defaultName }: { defaultName: string }) {
  const [open, setOpen] = useState(false);
  const [name, setName] = useState(defaultName);
  const [message, setMessage] = useState("");
  const [status, setStatus] = useState<"idle" | "sending" | "sent" | "error">("idle");

  const MAX = 1000;

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!message.trim()) return;
    setStatus("sending");
    const payload = {
      name: name.trim(),
      feedback: message.trim(),
      submitted_at: new Date().toISOString(),
    };
    try {
      const res = await fetch("/feedback", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      if (!res.ok) throw new Error("Bad response");
      setStatus("sent");
    } catch {
      // Fallback: open user's mail client to Dr. Scribner
      const subject = encodeURIComponent("Private feedback");
      const body = encodeURIComponent(
        `${payload.feedback}\n\n— ${payload.name || "Anonymous"}\n${payload.submitted_at}`
      );
      window.location.href = `mailto:${FEEDBACK_EMAIL}?subject=${subject}&body=${body}`;
      setStatus("sent");
    }
  }

  if (status === "sent") {
    return (
      <div
        style={{
          background: "var(--cream)",
          border: "1px solid var(--line)",
          borderRadius: 4,
          padding: "20px 18px",
          fontFamily: "'Cormorant Garamond', Georgia, serif",
          fontSize: 19,
          fontStyle: "italic",
          color: "var(--green-deep)",
          lineHeight: 1.4,
        }}
      >
        Thank you. Dr. Scribner will read this himself.
      </div>
    );
  }

  if (!open) {
    return (
      <button
        type="button"
        onClick={() => setOpen(true)}
        style={{
          background: "transparent",
          color: "var(--ink)",
          border: "1px solid var(--gold-deep)",
          padding: "12px 22px",
          borderRadius: 9999,
          fontFamily: "Inter, sans-serif",
          fontSize: 14,
          fontWeight: 500,
          cursor: "pointer",
          minHeight: 48,
          lineHeight: "22px",
          alignSelf: "center",
        }}
      >
        Write Dr. Scribner →
      </button>
    );
  }

  return (
    <form
      onSubmit={handleSubmit}
      style={{ display: "flex", flexDirection: "column", gap: 12, textAlign: "left" }}
    >
      <label style={{ fontFamily: "Inter, sans-serif", fontSize: 12, color: "var(--ink-2)" }}>
        Name (optional)
        <input
          type="text"
          value={name}
          onChange={(e) => setName(e.target.value)}
          maxLength={80}
          style={{
            display: "block",
            width: "100%",
            marginTop: 4,
            padding: "10px 12px",
            border: "1px solid var(--line)",
            borderRadius: 4,
            fontFamily: "Inter, sans-serif",
            fontSize: 14,
            background: "var(--cream)",
            color: "var(--ink)",
          }}
        />
      </label>
      <label style={{ fontFamily: "Inter, sans-serif", fontSize: 12, color: "var(--ink-2)" }}>
        Your message
        <textarea
          required
          value={message}
          onChange={(e) => setMessage(e.target.value.slice(0, MAX))}
          rows={5}
          maxLength={MAX}
          style={{
            display: "block",
            width: "100%",
            marginTop: 4,
            padding: "10px 12px",
            border: "1px solid var(--line)",
            borderRadius: 4,
            fontFamily: "Inter, sans-serif",
            fontSize: 14,
            background: "var(--cream)",
            color: "var(--ink)",
            resize: "vertical",
            minHeight: 110,
          }}
        />
        <span
          style={{
            display: "block",
            marginTop: 4,
            fontSize: 11,
            color: "var(--ink-3)",
            textAlign: "right",
          }}
        >
          {message.length}/{MAX}
        </span>
      </label>
      <button
        type="submit"
        disabled={status === "sending" || !message.trim()}
        style={{
          background: "var(--gold-deep)",
          color: "var(--cream)",
          border: "none",
          padding: "12px 22px",
          borderRadius: 9999,
          fontFamily: "Inter, sans-serif",
          fontSize: 14,
          fontWeight: 600,
          cursor: status === "sending" ? "wait" : "pointer",
          minHeight: 48,
          opacity: !message.trim() ? 0.6 : 1,
        }}
      >
        {status === "sending" ? "Sending…" : "Send to Dr. Scribner →"}
      </button>
    </form>
  );
}
