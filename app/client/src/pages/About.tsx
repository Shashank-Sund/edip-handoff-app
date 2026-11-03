import { ArrowDown, ArrowRight } from 'lucide-react';
import { Card } from '@/components/ui/card';
import { Layout } from '@/components/layout/Layout';
import { useApp } from '@/hooks/useApp';

const FLOW = [
  { title: 'Feeds', body: 'HL7 v2 ADT, results, orders and notes; Redox FHIR events through Zerobus; Clarity tables nightly.' },
  { title: 'Pipeline', body: 'Lakeflow declarative pipeline: parse, deduplicate across feeds, encounter state, handoff triggers.' },
  { title: 'Drafts', body: 'A serverless job builds a chart packet per trigger, one model call writes the narrative, rules check it.' },
  { title: 'Lakebase', body: 'Drafts sync to Postgres; edits, attestations, sign offs and acknowledgements are saved there.' },
  { title: 'This app', body: 'Board, handoff review, sign off and read back, per unit access, analytics.' },
];

function Block({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <Card className="space-y-2 p-5 text-sm leading-relaxed">
      <h2 className="text-base font-semibold">{title}</h2>
      {children}
    </Card>
  );
}

export default function About() {
  const { config } = useApp();
  return (
    <Layout title="How it works" crumbs="Reference app on Databricks">
      <div className="space-y-5">
        <Card className="p-5">
          <ol className="grid gap-3 md:grid-cols-5">
            {FLOW.map((f, i) => (
              <li key={f.title} className="relative rounded-lg border bg-muted/30 p-4">
                <div className="text-xs font-medium text-primary">Step {i + 1}</div>
                <div className="mt-0.5 font-semibold">{f.title}</div>
                <p className="mt-1 text-xs leading-relaxed text-muted-foreground">{f.body}</p>
                {i < FLOW.length - 1 && (
                  <>
                    <ArrowRight className="absolute -right-3 top-1/2 hidden size-4 -translate-y-1/2 text-muted-foreground md:block" aria-hidden />
                    <ArrowDown className="absolute -bottom-3 left-1/2 size-4 -translate-x-1/2 text-muted-foreground md:hidden" aria-hidden />
                  </>
                )}
              </li>
            ))}
          </ol>
        </Card>
        <div className="grid gap-5 lg:grid-cols-2">
          <Block title="What the model writes, and what it cannot">
            <p>
              Allergies, code status, isolation, vitals, labs, medications, pending results and destination come straight from the chart, so the model cannot misstate them. One model call writes the narrative elements, and every statement cites the chart records it used.
            </p>
            <p>
              Before anyone sees a draft, a validator removes citations to records that do not exist, and flags numbers that are not in the cited records. Illness severity has a rule floor: the model can raise it, never lower it.
            </p>
          </Block>
          <Block title="Sign off and read back">
            <p>
              Required items block sign off until the chart has them or the clinician attests from a fixed list. Vital signs must be recent for the patient's severity. The sender adds a callback; the receiver accepts with a read back in their own words. A draft that changes after signing is flagged until it is signed again.
            </p>
          </Block>
          <Block title="This demo">
            <p>
              All data is synthetic: about 700 ED visits over one week at two invented hospitals. Mock mode replays that week: move the clock in the top bar to see the board at any hour, and switch personas in the sidebar to see each role. Actions you take are kept in memory and can be cleared.
            </p>
            <p className="text-muted-foreground">
              Drafts: {config.drafts_from}
              {config.models.length ? ` (${config.models.join(', ')})` : ''}. Store: {config.store}.
            </p>
          </Block>
          <Block title="Credits">
            <p>
              Charts by{' '}
              <a href="https://www.tradingview.com/" className="font-medium text-primary hover:underline" target="_blank" rel="noreferrer">
                TradingView
              </a>{' '}
              Lightweight Charts and Recharts. Components adapted from shadcn/ui, built on Radix UI. Icons from Lucide.
            </p>
            <p className="text-muted-foreground">Templates draw on Joint Commission Sentinel Event Alert 58, IHI SBAR, I-PASS and its ED adaptation, SBAR-DR and PREP-4Cs.</p>
          </Block>
        </div>
      </div>
    </Layout>
  );
}
