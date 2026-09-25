"use client";

import Link from "next/link";
import { motion, useScroll, useTransform } from "motion/react";
import { ArrowRight, BadgeIndianRupee, FileCheck2, Gavel, Handshake, Layers3, ScrollText, ShieldCheck, Sparkles, Timer } from "lucide-react";
import { useRef } from "react";
import { Logo } from "@/components/logo";
import { Button, stagger } from "@/components/ui";
import { useLegal } from "@/lib/legal";

const ease = [0.16, 1, 0.3, 1] as const;

export default function Landing() {
  return (
    <div className="min-h-screen bg-[#07090c] text-[#f2f1ec]">
      <Nav />
      <Hero />
      <Proof />
      <HowItWorks />
      <LawSection />
      <Principles />
      <FinalCta />
      <footer className="border-t border-white/10 px-6 py-10 text-[13px] text-white/45">
        <div className="mx-auto flex max-w-6xl flex-wrap items-center justify-between gap-4">
          <Logo className="text-white/80" />
          <p>Legal calculations are simplified and not legal advice. Verify against the current MSMED Act text and RBI Bank Rate.</p>
        </div>
      </footer>
    </div>
  );
}

function Nav() {
  return (
    <header className="fixed inset-x-0 top-0 z-40">
      <div className="mx-auto mt-3 flex max-w-6xl items-center justify-between rounded-2xl border border-white/10 bg-black/30 px-4 py-2.5 backdrop-blur-xl">
        <Link href="/"><Logo /></Link>
        <nav className="hidden items-center gap-7 text-[14px] text-white/65 md:flex">
          <a href="#how" className="hover:text-white">How it works</a>
          <a href="#law" className="hover:text-white">The law</a>
          <a href="#principles" className="hover:text-white">Principles</a>
        </nav>
        <div className="flex items-center gap-2">
          <Link href="/login" className="rounded-xl px-3 py-2 text-[14px] text-white/75 hover:text-white">Sign in</Link>
          <Link href="/signup"><Button size="sm" className="bg-[#2dd4bf] text-[#04201d]">Start free</Button></Link>
        </div>
      </div>
    </header>
  );
}

function Aurora() {
  return (
    <div aria-hidden className="pointer-events-none absolute inset-0 overflow-hidden">
      <motion.div
        className="absolute -left-40 -top-40 size-[640px] rounded-full bg-[radial-gradient(circle,#0f766e_0%,transparent_65%)] opacity-60 blur-3xl"
        animate={{ x: [0, 80, -20, 0], y: [0, 40, 90, 0] }}
        transition={{ duration: 22, repeat: Infinity, ease: "easeInOut" }}
      />
      <motion.div
        className="absolute -right-32 top-20 size-[560px] rounded-full bg-[radial-gradient(circle,#2a78d6_0%,transparent_65%)] opacity-40 blur-3xl"
        animate={{ x: [0, -70, 10, 0], y: [0, 70, -30, 0] }}
        transition={{ duration: 26, repeat: Infinity, ease: "easeInOut" }}
      />
      <motion.div
        className="absolute bottom-[-260px] left-1/3 size-[620px] rounded-full bg-[radial-gradient(circle,#34d399_0%,transparent_65%)] opacity-25 blur-3xl"
        animate={{ x: [0, 60, -40, 0] }}
        transition={{ duration: 30, repeat: Infinity, ease: "easeInOut" }}
      />
      <div className="absolute inset-0 bg-[linear-gradient(to_right,rgb(255_255_255/0.04)_1px,transparent_1px),linear-gradient(to_bottom,rgb(255_255_255/0.04)_1px,transparent_1px)] bg-[size:64px_64px] [mask-image:radial-gradient(ellipse_at_center,black_30%,transparent_75%)]" />
    </div>
  );
}

function Hero() {
  const ref = useRef<HTMLDivElement>(null);
  const { scrollYProgress } = useScroll({ target: ref, offset: ["start start", "end start"] });
  const y = useTransform(scrollYProgress, [0, 1], [0, 120]);
  const fade = useTransform(scrollYProgress, [0, 0.8], [1, 0]);

  return (
    <section ref={ref} className="grain relative overflow-hidden px-6 pt-36 pb-24 sm:pt-44">
      <Aurora />
      <motion.div style={{ y, opacity: fade }} className="relative mx-auto grid max-w-6xl items-center gap-14 lg:grid-cols-[1.1fr_0.9fr]">
        <motion.div variants={stagger.container} initial="hidden" animate="show">
          <motion.div variants={stagger.item} className="mb-6 inline-flex items-center gap-2 rounded-full border border-white/15 bg-white/5 px-3 py-1 text-[13px] text-white/75">
            <Sparkles className="size-3.5 text-[#2dd4bf]" /> Built for Indian MSMEs
          </motion.div>
          <motion.h1 variants={stagger.item} className="font-display text-[52px] leading-[0.98] tracking-tight sm:text-[76px]">
            Get paid what you’re owed.
            <span className="block italic text-transparent [background:linear-gradient(90deg,#2dd4bf,#86b6ef)] [-webkit-background-clip:text] [background-clip:text]">
              Politely. Legally. On time.
            </span>
          </motion.h1>
          <motion.p variants={stagger.item} className="mt-6 max-w-xl text-[17px] leading-relaxed text-white/65">
            Recova watches every invoice, scores each buyer from how they really pay, works out your rights under the MSMED Act to the rupee — and tells you the right next step. Every action it suggests is on the record.
          </motion.p>
          <motion.div variants={stagger.item} className="mt-9 flex flex-wrap gap-3">
            <Link href="/signup">
              <Button size="lg" className="bg-[#2dd4bf] text-[#04201d]" icon={<ArrowRight className="size-4 transition-transform group-hover:translate-x-0.5" />}>
                Start recovering
              </Button>
            </Link>
            <a href="#how"><Button size="lg" variant="ghost" className="text-white/80 hover:bg-white/10 hover:text-white">See how it works</Button></a>
          </motion.div>
        </motion.div>
        <HeroCards />
      </motion.div>
    </section>
  );
}

function Float({ children, delay = 0, className }: { children: React.ReactNode; delay?: number; className?: string }) {
  return (
    <motion.div
      initial={{ opacity: 0, y: 30, scale: 0.96 }}
      animate={{ opacity: 1, y: 0, scale: 1 }}
      transition={{ duration: 0.9, delay, ease }}
      className={className}
    >
      <motion.div animate={{ y: [0, -8, 0] }} transition={{ duration: 6 + delay * 2, repeat: Infinity, ease: "easeInOut", delay }}>
        {children}
      </motion.div>
    </motion.div>
  );
}

function HeroCards() {
  return (
    <div className="relative h-[440px]">
      <Float delay={0.2} className="absolute left-0 top-4 w-[300px]">
        <div className="rounded-2xl border border-white/12 bg-white/[0.06] p-5 shadow-2xl backdrop-blur-xl">
          <div className="flex items-center justify-between text-[12px] text-white/50"><span>INV-2026-0142</span><span className="rounded-full bg-[#d03b3b]/20 px-2 py-0.5 text-[#ef7c7c]">38 days overdue</span></div>
          <div className="mt-3 text-[13px] text-white/55">Vistara Consumer Goods</div>
          <div className="mt-1 font-mono text-3xl tracking-tight">₹4,82,300</div>
          <div className="mt-4 h-1.5 overflow-hidden rounded-full bg-white/10">
            <motion.div className="h-full rounded-full bg-gradient-to-r from-[#fab219] to-[#ec835a]" initial={{ width: "0%" }} animate={{ width: "72%" }} transition={{ duration: 1.6, delay: 0.8, ease }} />
          </div>
          <div className="mt-2 flex justify-between text-[12px] text-white/45"><span>Statutory due date passed</span><span>Interest accruing</span></div>
        </div>
      </Float>

      <Float delay={0.45} className="absolute right-0 top-28 w-[290px]">
        <div className="rounded-2xl border border-[#2dd4bf]/30 bg-[#0f2e2b]/70 p-5 shadow-2xl backdrop-blur-xl">
          <div className="flex items-center gap-2 text-[12px] font-medium tracking-wide text-[#2dd4bf] uppercase"><Gavel className="size-3.5" /> Today’s decision</div>
          <div className="mt-2 text-[15px] font-medium">Send a firm reminder</div>
          <p className="mt-1.5 text-[13px] leading-relaxed text-white/60">Poor payment history, well past the statutory limit. State the due date and the interest now running.</p>
          <div className="mt-3 flex gap-1.5">
            {[1, 2, 3, 4].map((r) => (
              <motion.span key={r} className={`h-1.5 flex-1 rounded-full ${r <= 2 ? "bg-[#2dd4bf]" : "bg-white/15"}`} initial={{ scaleX: 0 }} animate={{ scaleX: 1 }} transition={{ delay: 1 + r * 0.12, duration: 0.5 }} style={{ originX: 0 }} />
            ))}
          </div>
          <div className="mt-1.5 text-[11px] text-white/45">Rung 2 of 4 · within the legal ceiling</div>
        </div>
      </Float>

      <Float delay={0.7} className="absolute bottom-2 left-10 w-[330px]">
        <div className="rounded-2xl border border-white/12 bg-white/[0.06] p-4 shadow-2xl backdrop-blur-xl">
          <div className="flex items-center gap-2 text-[12px] text-white/50"><ScrollText className="size-3.5" /> Audit trail</div>
          <ul className="mt-2 space-y-2 text-[12.5px]">
            {[
              ["rule", "Promise recorded — holding off until 5 Oct"],
              ["rule", "Dispute detected — handed to a person"],
              ["user", "Payment of ₹1,20,000 received"],
            ].map(([src, text], i) => (
              <motion.li key={text} initial={{ opacity: 0, x: -10 }} animate={{ opacity: 1, x: 0 }} transition={{ delay: 1.3 + i * 0.25 }} className="flex items-center gap-2 text-white/75">
                <span className="rounded bg-white/10 px-1.5 py-0.5 font-mono text-[10px] text-white/55 uppercase">{src}</span>{text}
              </motion.li>
            ))}
          </ul>
        </div>
      </Float>
    </div>
  );
}

function Reveal({ children, className, delay = 0 }: { children: React.ReactNode; className?: string; delay?: number }) {
  return (
    <motion.div
      initial={{ opacity: 0, y: 28 }}
      whileInView={{ opacity: 1, y: 0 }}
      viewport={{ once: true, margin: "-80px" }}
      transition={{ duration: 0.7, delay, ease }}
      className={className}
    >
      {children}
    </motion.div>
  );
}

function Proof() {
  // Statutory figures come from config/legal.yaml via the API -- never typed here.
  const legal = useLegal();
  const items = [
    { icon: Timer, title: "Knows the real due date", body: legal
        ? `Under Section 15 a written term can’t exceed ${legal.max_agreement_days} days. Recova counts from the statutory date, not the one the buyer asked for.`
        : "Under Section 15 a written payment term has a legal ceiling. Recova counts from the statutory date, not the one the buyer asked for." },
    { icon: BadgeIndianRupee, title: "Interest, to the rupee", body: legal
        ? `Compound interest with monthly rests at ${legal.bank_rate_multiplier}× the RBI bank rate (${legal.effective_annual_rate_pct}% a year as of ${legal.as_of}), worked out per invoice and per payment.`
        : "Compound interest with monthly rests at a multiple of the RBI bank rate, worked out per invoice and per payment." },
    { icon: ShieldCheck, title: "Knows when to stop", body: "Opt-outs, disputes, message limits and quiet hours are hard rules — never left to an AI’s judgement." },
  ];
  return (
    <section className="relative px-6 py-24">
      <div className="mx-auto grid max-w-6xl gap-5 md:grid-cols-3">
        {items.map(({ icon: Icon, title, body }, i) => (
          <Reveal key={title} delay={i * 0.08}>
            <div className="group h-full rounded-2xl border border-white/10 bg-white/[0.03] p-6 transition-colors hover:border-[#2dd4bf]/40 hover:bg-white/[0.05]">
              <div className="mb-4 grid size-10 place-items-center rounded-xl bg-[#2dd4bf]/12 text-[#2dd4bf] transition-transform group-hover:-rotate-6 group-hover:scale-110"><Icon className="size-5" /></div>
              <h3 className="text-[17px] font-semibold tracking-tight">{title}</h3>
              <p className="mt-2 text-[14.5px] leading-relaxed text-white/60">{body}</p>
            </div>
          </Reveal>
        ))}
      </div>
    </section>
  );
}

function HowItWorks() {
  const steps = [
    { icon: Layers3, title: "Bring your book", body: "Add buyers and invoices, or import a CSV from your accounting software." },
    { icon: Gavel, title: "Every invoice, judged", body: "Each buyer is scored from real payment history; each invoice gets its legal position." },
    { icon: Handshake, title: "One clear next step", body: "Wait, a gentle nudge, a firm reminder, the legal facts — or hand it to a person." },
    { icon: FileCheck2, title: "Everything on record", body: "Promises, payments, disputes and approvals land in an audit trail you can show anyone." },
  ];
  return (
    <section id="how" className="px-6 py-24">
      <div className="mx-auto max-w-6xl">
        <Reveal>
          <p className="text-[13px] font-medium tracking-[0.16em] text-[#2dd4bf] uppercase">How it works</p>
          <h2 className="mt-3 max-w-2xl font-display text-[44px] leading-[1.02] tracking-tight sm:text-[56px]">From a messy receivables book to a calm daily list.</h2>
        </Reveal>
        <div className="relative mt-14 grid gap-6 md:grid-cols-4">
          <motion.div
            aria-hidden
            className="absolute left-0 right-0 top-5 hidden h-px bg-gradient-to-r from-[#2dd4bf]/0 via-[#2dd4bf]/60 to-[#2dd4bf]/0 md:block"
            initial={{ scaleX: 0 }} whileInView={{ scaleX: 1 }} viewport={{ once: true }} transition={{ duration: 1.4, ease }}
          />
          {steps.map(({ icon: Icon, title, body }, i) => (
            <Reveal key={title} delay={0.15 + i * 0.12}>
              <div className="relative">
                <div className="relative z-10 grid size-10 place-items-center rounded-full border border-[#2dd4bf]/40 bg-[#07090c] text-[#2dd4bf]"><Icon className="size-4.5" /></div>
                <div className="mt-5 font-mono text-[12px] text-white/40">0{i + 1}</div>
                <h3 className="mt-1 text-[17px] font-semibold tracking-tight">{title}</h3>
                <p className="mt-2 text-[14.5px] leading-relaxed text-white/60">{body}</p>
              </div>
            </Reveal>
          ))}
        </div>
      </div>
    </section>
  );
}

function LawSection() {
  const rungs = [
    { n: 1, name: "Soft nudge", text: "A courtesy reminder. No legal language at all." },
    { n: 2, name: "Firm", text: "The statutory due date and the interest now accruing." },
    { n: 3, name: "Legal facts", text: "The buyer’s own tax cost of paying late, and their disclosure duty." },
    { n: 4, name: "Hand to a person", text: "Stop messaging. A ready-to-file Samadhaan draft, for you to decide on." },
  ];
  return (
    <section id="law" className="px-6 py-24">
      <div className="mx-auto grid max-w-6xl gap-14 lg:grid-cols-2">
        <Reveal>
          <p className="text-[13px] font-medium tracking-[0.16em] text-[#2dd4bf] uppercase">The law is on your side</p>
          <h2 className="mt-3 font-display text-[44px] leading-[1.02] tracking-tight sm:text-[52px]">
            A four-step ladder that never says more than the law allows.
          </h2>
          <p className="mt-5 max-w-lg text-[16px] leading-relaxed text-white/60">
            Every step is capped by what is legally true for that invoice today. Messages state facts — never threats — and every number comes from one audited table of legal figures.
          </p>
        </Reveal>
        <div className="space-y-3">
          {rungs.map((r, i) => (
            <Reveal key={r.n} delay={i * 0.1}>
              <div className="flex items-start gap-4 rounded-2xl border border-white/10 bg-white/[0.03] p-5">
                <div className="relative grid size-11 shrink-0 place-items-center rounded-xl bg-white/[0.06] font-mono text-lg">
                  {r.n}
                  <motion.span className="absolute inset-0 rounded-xl border border-[#2dd4bf]" initial={{ opacity: 0 }} whileInView={{ opacity: [0, 1, 0.25] }} viewport={{ once: true }} transition={{ duration: 1.2, delay: 0.3 + i * 0.25 }} />
                </div>
                <div>
                  <div className="text-[16px] font-semibold tracking-tight">{r.name}</div>
                  <div className="mt-1 text-[14px] text-white/60">{r.text}</div>
                </div>
              </div>
            </Reveal>
          ))}
        </div>
      </div>
    </section>
  );
}

function Principles() {
  const rows = [
    ["Rules decide", "Due dates, interest, escalation and every stop are deterministic code you can read."],
    ["AI writes", "Language models draft messages and read replies — inside guardrails that check every figure."],
    ["You approve", "Nothing goes to a buyer without you. Every approval is recorded with its reason."],
    ["Your data stays yours", "Each business is fully isolated from every other on the platform."],
  ];
  return (
    <section id="principles" className="px-6 py-24">
      <div className="mx-auto max-w-6xl">
        <Reveal><h2 className="font-display text-[44px] tracking-tight sm:text-[52px]">Principles, not promises.</h2></Reveal>
        <div className="mt-10 grid gap-px overflow-hidden rounded-2xl border border-white/10 bg-white/10 sm:grid-cols-2">
          {rows.map(([title, body], i) => (
            <Reveal key={title} delay={i * 0.06} className="bg-[#07090c] p-7">
              <h3 className="text-[18px] font-semibold tracking-tight">{title}</h3>
              <p className="mt-2 text-[14.5px] leading-relaxed text-white/60">{body}</p>
            </Reveal>
          ))}
        </div>
      </div>
    </section>
  );
}

function FinalCta() {
  return (
    <section className="px-6 pb-28">
      <Reveal>
        <div className="grain relative mx-auto max-w-6xl overflow-hidden rounded-3xl border border-white/10 bg-gradient-to-br from-[#0f766e] via-[#0d4f4a] to-[#0b1f33] px-8 py-16 text-center sm:px-16">
          <h2 className="font-display text-[42px] leading-tight tracking-tight sm:text-[56px]">Your money, back where it belongs.</h2>
          <p className="mx-auto mt-4 max-w-xl text-[16px] text-white/70">Set up in minutes. Load a demo book to see it work before you add your own.</p>
          <Link href="/signup" className="mt-8 inline-block">
            <Button size="lg" className="bg-white text-[#07090c]" icon={<ArrowRight className="size-4 transition-transform group-hover:translate-x-0.5" />}>Create your account</Button>
          </Link>
        </div>
      </Reveal>
    </section>
  );
}
