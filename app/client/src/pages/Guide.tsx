import { useEffect, useRef, useState, type ReactNode } from 'react';
import { ArrowDown, ArrowRight, ChevronDown, CircleHelp, ListTree } from 'lucide-react';
import { Card } from '@/components/ui/card';
import { Layout } from '@/components/layout/Layout';
import { Pill, ProgressRing, SeverityBadge, StateBadge } from '@/components/common/badges';
import { GUIDE, type GuideSection } from '@/components/guide/content';
import { useApp } from '@/hooks/useApp';
import { cn } from '@/lib/utils';

const FLOW = [
  { title: 'Hospital systems', body: 'ADT, results, orders and notes (HL7 v2), FHIR events, and a nightly copy of the EHR database.' },
  { title: 'Data pipeline', body: 'Reads every feed, removes duplicates, and keeps each patient\'s current status.' },
  { title: 'Drafts', body: 'At each admit decision or important change, the model writes the narrative; rules check it.' },
  { title: 'App database', body: 'Holds the drafts for the app, and every edit, attestation, signature and acceptance.' },
  { title: 'This app', body: 'Board, handoff review, sign off, read back and analytics.' },
];

const REFERENCE = GUIDE.filter((s) => s.kind === 'reference');
const byKind = (k: GuideSection['kind']) => GUIDE.filter((s) => s.kind === k);

/** The table of contents: parts in reading order, with the page by page reference nested. */
const TOC: { id: string; label: string; children?: { id: string; label: string }[] }[] = [
  ...GUIDE.filter((s) => s.kind !== 'reference' && s.kind !== 'glossary').map((s) => ({ id: s.id, label: s.nav ?? s.title })),
  { id: 'reference', label: 'Page by page', children: REFERENCE.map((s) => ({ id: s.id, label: s.nav ?? s.title })) },
  ...byKind('glossary').map((s) => ({ id: s.id, label: s.title })),
  { id: 'about', label: 'About this demo' },
];
const TOC_IDS = TOC.flatMap((t) => [t.id, ...(t.children ?? []).map((c) => c.id)]);

function scrollToHash() {
  const id = decodeURIComponent(window.location.hash.slice(1));
  if (id) window.setTimeout(() => document.getElementById(id)?.scrollIntoView({ block: 'start' }), 50);
}

/** The id of the part the reader is in: the last one whose top has passed under the header. */
function useActive(): string {
  const [active, setActive] = useState(TOC_IDS[0]);
  useEffect(() => {
    let frame = 0;
    const update = () => {
      frame = 0;
      let current = TOC_IDS[0];
      for (const id of TOC_IDS) {
        const el = document.getElementById(id);
        if (el && el.getBoundingClientRect().top <= 120) current = id;
      }
      setActive(current);
    };
    const on = () => {
      if (!frame) frame = window.requestAnimationFrame(update);
    };
    update();
    window.addEventListener('scroll', on, { passive: true });
    return () => {
      window.removeEventListener('scroll', on);
      if (frame) window.cancelAnimationFrame(frame);
    };
  }, []);
  return active;
}

function TocList({ active, onPick }: { active: string; onPick?: () => void }) {
  const link = (id: string, label: string, nested = false) => {
    const current = active === id;
    return (
      <a
        href={`#${id}`}
        onClick={onPick}
        aria-current={current ? 'location' : undefined}
        className={cn(
          'block rounded-md px-2.5 py-1.5 outline-none transition-colors focus-visible:ring-[3px] focus-visible:ring-ring/50',
          nested && 'py-1 pl-5 text-[13px]',
          current ? 'bg-accent font-medium text-foreground' : 'text-muted-foreground hover:bg-accent hover:text-foreground',
        )}
      >
        {label}
      </a>
    );
  };
  return (
    <ul className="space-y-0.5 text-sm">
      {TOC.map((t) => (
        <li key={t.id}>
          {link(t.id, t.label)}
          {t.children && (
            <ul className="mt-0.5 space-y-0.5">
              {t.children.map((c) => (
                <li key={c.id}>{link(c.id, c.label, true)}</li>
              ))}
            </ul>
          )}
        </li>
      ))}
    </ul>
  );
}

/** At phone and tablet width: a compact, collapsible contents list at the top of the page. */
function PhoneToc({ active }: { active: string }) {
  const ref = useRef<HTMLDetailsElement>(null);
  const label = TOC.flatMap((t) => [t, ...(t.children ?? [])]).find((t) => t.id === active)?.label;
  return (
    <nav aria-label="Guide contents" className="lg:hidden">
      <details ref={ref} className="group rounded-xl border bg-card shadow-card">
        <summary className="flex cursor-pointer list-none items-center gap-2 rounded-xl px-4 py-3 text-sm outline-none focus-visible:ring-[3px] focus-visible:ring-ring/50 [&::-webkit-details-marker]:hidden">
          <ListTree className="size-4 text-muted-foreground" aria-hidden />
          <span className="font-medium">Contents</span>
          {label && <span className="min-w-0 truncate text-muted-foreground">· {label}</span>}
          <ChevronDown className="ml-auto size-4 text-muted-foreground transition-transform group-open:rotate-180" aria-hidden />
        </summary>
        <div className="border-t px-2 py-2">
          <TocList active={active} onPick={() => ref.current?.removeAttribute('open')} />
        </div>
      </details>
    </nav>
  );
}

/** At wide screens, a quick key to the badges, kept in view beside the text. */
function AtAGlance() {
  const row = (label: string, children: ReactNode) => (
    <div className="space-y-1.5">
      <div className="text-xs font-medium text-muted-foreground">{label}</div>
      <div className="flex flex-wrap gap-1.5">{children}</div>
    </div>
  );
  return (
    <aside aria-label="Badges at a glance" className="hidden min-[87.5rem]:block">
      <Card className="sticky top-20 space-y-4 p-4 text-sm">
        <h2 className="font-semibold">At a glance</h2>
        {row('Illness severity', <><SeverityBadge value="stable" /><SeverityBadge value="watcher" /><SeverityBadge value="unstable" /></>)}
        {row('Handoff status', <><StateBadge state="draft" /><StateBadge state="signed" /><StateBadge state="acknowledged" /><StateBadge state="signed" changed /></>)}
        {row(
          'Completeness',
          <>
            <span className="inline-flex items-center gap-1.5 text-xs"><ProgressRing value={0.58} size={18} stroke={3} blocking={4} />4 open</span>
            <span className="inline-flex items-center gap-1.5 text-xs"><ProgressRing value={1} size={18} stroke={3} />ready</span>
          </>,
        )}
        {row('Needs you', <><Pill tone="danger">Required</Pill><Pill tone="warn">Stale</Pill><Pill tone="warn">Changed</Pill></>)}
        <p className="flex gap-2 border-t pt-3 text-xs text-muted-foreground">
          <CircleHelp className="size-4 shrink-0" aria-hidden />
          Press ? next to any title in the app to read about it here.
        </p>
      </Card>
    </aside>
  );
}

function PartHeading({ id, title, summary, level = 2 }: { id?: string; title: string; summary: string; level?: 2 | 3 }) {
  const H = level === 2 ? 'h2' : 'h3';
  return (
    <div id={id} className="scroll-mt-20">
      <H className={cn('font-semibold tracking-tight', level === 2 ? 'text-xl' : 'text-base')}>{title}</H>
      <p className="mt-0.5 text-sm text-muted-foreground">{summary}</p>
    </div>
  );
}

function Intro({ s }: { s: GuideSection }) {
  const [lead, ...rest] = s.topics;
  return (
    <section id={s.id} aria-labelledby={`${s.id}-h`} className="scroll-mt-20">
      <Card className="space-y-5 p-5 sm:p-6">
        <div id={`${s.id}-${lead.id}`} className="scroll-mt-20 space-y-2">
          <p className="text-xs font-semibold uppercase tracking-wide text-primary" id={`${s.id}-h`}>{s.title}</p>
          <h2 className="text-xl font-semibold tracking-tight">{lead.title}</h2>
          <div className="space-y-2 text-[15px] leading-relaxed">{lead.body}</div>
        </div>
        {rest.map((t) => (
          <div key={t.id} id={`${s.id}-${t.id}`} className="scroll-mt-20 space-y-2 border-t pt-5 text-sm leading-relaxed">
            <h3 className="font-semibold">{t.title}</h3>
            {t.body}
          </div>
        ))}
      </Card>
    </section>
  );
}

function Steps({ s }: { s: GuideSection }) {
  return (
    <section id={s.id} className="scroll-mt-20 space-y-4">
      <PartHeading title={s.title} summary={s.summary} />
      <ol className="relative space-y-3 before:absolute before:inset-y-4 before:left-[1.4rem] before:w-px before:bg-border" aria-label="Walkthrough steps">
        {s.topics.map((t, i) => (
          <li key={t.id} id={`${s.id}-${t.id}`} className="relative scroll-mt-20">
            <Card className="flex-row gap-3 p-4 sm:gap-4">
              <span className="relative grid size-7 shrink-0 place-items-center rounded-full bg-primary text-xs font-semibold text-primary-foreground" aria-hidden>
                {i + 1}
              </span>
              <div className="min-w-0 flex-1 space-y-2.5 text-sm leading-relaxed">
                <h3 className="pt-0.5 font-semibold">
                  <span className="sr-only">Step {i + 1}: </span>
                  {t.title}
                </h3>
                {t.body}
              </div>
            </Card>
          </li>
        ))}
      </ol>
    </section>
  );
}

function CardsPart({ s, icon }: { s: GuideSection; icon?: ReactNode }) {
  return (
    <section id={s.id} className="scroll-mt-20 space-y-4">
      <PartHeading title={s.title} summary={s.summary} />
      <div className={cn('grid gap-4', s.kind === 'roles' && '@2xl:grid-cols-2')}>
        {s.topics.map((t) => (
          <Card key={t.id} id={`${s.id}-${t.id}`} className="scroll-mt-20 gap-3 p-5 text-sm leading-relaxed">
            <h3 className="flex items-start gap-2 font-semibold">
              {icon}
              {t.title}
            </h3>
            {t.body}
          </Card>
        ))}
      </div>
    </section>
  );
}

function Reference() {
  return (
    <section id="reference" className="scroll-mt-20 space-y-6" aria-labelledby="reference-h">
      <div>
        <h2 id="reference-h" className="text-xl font-semibold tracking-tight">Page by page</h2>
        <p className="mt-0.5 text-sm text-muted-foreground">Every part of the screen, with the labels you will see. Come back here when you need a detail.</p>
      </div>
      {REFERENCE.map((s) => (
        <section key={s.id} id={s.id} className="scroll-mt-20 space-y-3" aria-labelledby={`${s.id}-h`}>
          <div>
            <h3 id={`${s.id}-h`} className="text-base font-semibold tracking-tight">{s.title}</h3>
            <p className="text-sm text-muted-foreground">{s.summary}</p>
          </div>
          <Card className="divide-y overflow-hidden">
            {s.topics.map((t) => (
              <div key={t.id} id={`${s.id}-${t.id}`} className="scroll-mt-20 space-y-3 p-5 text-sm leading-relaxed">
                <h4 className="font-semibold">{t.title}</h4>
                {t.body}
              </div>
            ))}
          </Card>
        </section>
      ))}
    </section>
  );
}

function Glossary({ s }: { s: GuideSection }) {
  return (
    <section id={s.id} className="scroll-mt-20 space-y-4">
      <PartHeading title={s.title} summary={s.summary} />
      <Card className="p-2 sm:p-3">
        <dl className="grid text-sm leading-relaxed @2xl:grid-cols-2">
          {s.topics.map((t) => (
            <div key={t.id} id={`${s.id}-${t.id}`} className="scroll-mt-20 rounded-lg px-3 py-2.5 target:bg-primary/5 target:ring-1 target:ring-primary/20">
              <dt className="font-semibold">{t.title}</dt>
              <dd className="text-muted-foreground">{t.body}</dd>
            </div>
          ))}
        </dl>
      </Card>
    </section>
  );
}

function About() {
  const { config } = useApp();
  return (
    <section id="about" className="scroll-mt-20 space-y-4">
      <PartHeading title="About this demo" summary="Where the data comes from, and credits." />
      <Card className="space-y-2 p-5 text-sm leading-relaxed">
        <h3 className="font-semibold">The demo</h3>
        <p>About 700 made up ED visits over one week at two invented hospitals, Shai General Hospital (SGH) and Lakeside Community Hospital (LKS). Drafts: {config.drafts_from}{config.models.length ? ` (${config.models.join(', ')})` : ''}.</p>
        <p className="text-muted-foreground">
          Charts by{' '}
          <a href="https://www.tradingview.com/" className="font-medium text-primary hover:underline" target="_blank" rel="noreferrer">
            TradingView
          </a>{' '}
          Lightweight Charts and Recharts; components adapted from shadcn/ui on Radix UI; icons from Lucide. Templates draw on Joint Commission Sentinel Event Alert 58, IHI SBAR, I-PASS and its ED adaptation, SBAR-DR and PREP-4Cs.
        </p>
        <p className="text-xs text-muted-foreground">TradingView Lightweight Charts™ Copyright (с) 2025 TradingView, Inc. https://www.tradingview.com/</p>
      </Card>
      <Card className="space-y-3 p-5">
        <div>
          <h3 className="text-sm font-semibold">How the app gets its data</h3>
          <p className="text-xs text-muted-foreground">For the curious, and for informatics teams.</p>
        </div>
        <ol className="grid gap-3 @2xl:grid-cols-5">
          {FLOW.map((f, i) => (
            <li key={f.title} className="relative rounded-lg border bg-muted/30 p-3">
              <div className="text-xs font-medium text-primary">Step {i + 1}</div>
              <div className="mt-0.5 text-sm font-semibold">{f.title}</div>
              <p className="mt-1 text-xs leading-relaxed text-muted-foreground">{f.body}</p>
              {i < FLOW.length - 1 && (
                <>
                  <ArrowRight className="absolute -right-3 top-1/2 hidden size-4 -translate-y-1/2 text-muted-foreground @2xl:block" aria-hidden />
                  <ArrowDown className="absolute -bottom-3 left-1/2 size-4 -translate-x-1/2 text-muted-foreground @2xl:hidden" aria-hidden />
                </>
              )}
            </li>
          ))}
        </ol>
      </Card>
    </section>
  );
}

export default function Guide() {
  const active = useActive();
  useEffect(() => {
    scrollToHash();
    // In app links (the ? panels, links inside the guide) change the hash without reloading the page.
    window.addEventListener('popstate', scrollToHash);
    window.addEventListener('hashchange', scrollToHash);
    return () => {
      window.removeEventListener('popstate', scrollToHash);
      window.removeEventListener('hashchange', scrollToHash);
    };
  }, []);
  return (
    <Layout title="How to use" crumbs="A guide for nurses and physicians">
      <div className="grid gap-6 lg:grid-cols-[200px_minmax(0,1fr)] lg:gap-8 min-[87.5rem]:grid-cols-[200px_minmax(0,1fr)_240px]">
        <nav aria-label="Guide contents" className="hidden lg:block">
          <div className="sticky top-20 max-h-[calc(100dvh-6rem)] overflow-y-auto pb-4">
            <p className="mb-2 px-2.5 text-xs font-semibold uppercase tracking-wide text-muted-foreground">On this page</p>
            <TocList active={active} />
          </div>
        </nav>
        <div className="@container min-w-0 max-w-3xl space-y-12">
          <PhoneToc active={active} />
          {GUIDE.map((s) => {
            if (s.kind === 'intro') return <Intro key={s.id} s={s} />;
            if (s.kind === 'steps') return <Steps key={s.id} s={s} />;
            if (s.kind === 'roles') return <CardsPart key={s.id} s={s} />;
            if (s.kind === 'questions') return <CardsPart key={s.id} s={s} icon={<CircleHelp className="mt-0.5 size-4 shrink-0 text-primary" aria-hidden />} />;
            if (s.kind === 'glossary') return <Glossary key={s.id} s={s} />;
            if (s.kind === 'reference' && s === REFERENCE[0]) return <Reference key="reference" />;
            return null;
          })}
          <About />
        </div>
        <AtAGlance />
      </div>
    </Layout>
  );
}
