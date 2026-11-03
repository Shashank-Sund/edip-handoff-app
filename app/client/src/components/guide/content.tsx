/**
 * The user guide, written for nurses and physicians. One source for the How to use page and for the ? buttons,
 * which open a single topic in a side panel. Topic ids are "<section>" or "<section>.<topic>".
 */
import type { ReactNode } from 'react';
import {
  CalendarClock,
  Check,
  ChevronLeft,
  ChevronRight,
  PanelLeftClose,
  Pencil,
  Play,
  Sparkles,
} from 'lucide-react';
import { Pill, ProgressRing, SeverityBadge, StateBadge } from '@/components/common/badges';

export interface Topic {
  id: string;
  title: string;
  body: ReactNode;
}

export interface GuideSection {
  id: string;
  title: string;
  summary: string;
  topics: Topic[];
}

function Key({ children }: { children: ReactNode }) {
  return <span className="inline-flex items-center gap-1 rounded-md border bg-card px-1.5 py-0.5 align-middle text-xs font-medium [&>svg]:size-3.5">{children}</span>;
}

function Steps({ items }: { items: ReactNode[] }) {
  return (
    <ol className="space-y-2">
      {items.map((t, i) => (
        <li key={i} className="flex gap-3">
          <span className="mt-0.5 grid size-5 shrink-0 place-items-center rounded-full bg-primary/10 text-[11px] font-semibold text-primary">{i + 1}</span>
          <span>{t}</span>
        </li>
      ))}
    </ol>
  );
}

function Terms({ items }: { items: [ReactNode, ReactNode][] }) {
  return (
    <dl className="divide-y rounded-lg border">
      {items.map(([k, v], i) => (
        <div key={i} className="grid gap-1 px-3 py-2.5 sm:grid-cols-[11rem_1fr] sm:gap-4">
          <dt className="font-medium">{k}</dt>
          <dd className="text-muted-foreground">{v}</dd>
        </div>
      ))}
    </dl>
  );
}

function Note({ children }: { children: ReactNode }) {
  return <p className="rounded-lg bg-primary/5 px-3 py-2.5 text-[13px] text-foreground ring-1 ring-inset ring-primary/15">{children}</p>;
}

export const GUIDE: GuideSection[] = [
  {
    id: 'start',
    title: 'Start here',
    summary: 'What the app does, who does what, and the five steps of a handoff.',
    topics: [
      {
        id: 'what',
        title: 'What this app does',
        body: (
          <>
            <p>
              When the ED decides to admit a patient, the app writes a draft handoff for you from the chart: the patient's story, what was done in the ED, what is pending, what could go wrong and what to do about it. You review it, fix anything that is wrong, and sign it. The nurse or physician on the receiving unit reads it and accepts it with a short read back.
            </p>
            <p>The app never replaces talking to each other. It makes sure nothing important is missing before you do.</p>
          </>
        ),
      },
      {
        id: 'roles',
        title: 'Who does what',
        body: (
          <Terms
            items={[
              ['ED nurse', 'Reviews and signs the nurse handoff (SBAR). Sees every patient waiting for a bed.'],
              ['ED physician', 'Reviews and signs the physician handoff (I-PASS). Sees every patient.'],
              ['Receiving nurse', 'Sees only the patients coming to their unit. Reads the nurse handoff and accepts it with a read back.'],
              ['Admitting physician', 'Reads the physician handoff and accepts it with a read back.'],
            ]}
          />
        ),
      },
      {
        id: 'flow',
        title: 'The five steps of a handoff',
        body: (
          <Steps
            items={[
              <>The ED decides to admit. Within moments a draft appears on the Board with the status <StateBadge state="draft" />.</>,
              <>The ED clinician opens it, reads the model's text, corrects what is wrong, and clears every amber item (for example "No known allergies confirmed").</>,
              <>The ED clinician adds a callback number and signs. The status becomes <StateBadge state="signed" />.</>,
              <>The receiving clinician reads it and accepts it with a read back. The status becomes <StateBadge state="acknowledged" />.</>,
              <>If something important changes (a new result, a bed change, a signed note), the app writes a new version and flags it <StateBadge state="signed" changed /> so the new information is read and signed again. Once the patient leaves the ED the handoff is final.</>,
            ]}
          />
        ),
      },
    ],
  },
  {
    id: 'topbar',
    title: 'The top bar and the clock',
    summary: 'The page title, the demo label and the clock that decides which moment you are looking at.',
    topics: [
      {
        id: 'clock',
        title: 'The date and time at the top right',
        body: (
          <>
            <p>
              This demo uses one past week of made up patients. The date and time <Key><CalendarClock />Wed, Oct 28 16:00</Key> is the moment you are looking at. Everything on every page is shown as it was at that moment: who is in the ED, who is waiting for a bed, the latest draft of each handoff, and what has been signed. Times are the hospital's local time.
            </p>
            <Terms
              items={[
                [<><Key><ChevronLeft /></Key> <Key><ChevronRight /></Key></>, 'Go back or forward one hour.'],
                [<Key><Play />Play</Key>, 'Moves the clock forward 15 minutes every 1.5 seconds, so you can watch patients arrive, get beds and move up to the units. Press again to pause.'],
                [<Key><CalendarClock />Date</Key>, 'Opens a panel: drag the slider to any time in the week, pick a day, jump to the busiest hour, or clear the actions you took in the demo.'],
              ]}
            />
            <Note>In a hospital the clock reads Live and always shows the current time; there is nothing to set.</Note>
          </>
        ),
      },
      {
        id: 'label',
        title: 'Synthetic data',
        body: <p>Every patient, name and number in this demo is invented. Nothing comes from a real hospital.</p>,
      },
    ],
  },
  {
    id: 'sidebar',
    title: 'The sidebar',
    summary: 'Pages, the light or dark theme, and who you are signed in as.',
    topics: [
      {
        id: 'pages',
        title: 'Pages',
        body: (
          <Terms
            items={[
              ['Board', 'Your work list: every patient with a handoff right now. Start here.'],
              ['Analytics', 'How the week has gone: how busy the ED was, how long patients waited for beds, what handoffs were missing.'],
              ['Templates', 'What each kind of handoff contains and which items must be filled before signing.'],
              ['How to use', 'This guide.'],
            ]}
          />
        ),
      },
      {
        id: 'you',
        title: 'Who you are, and the other buttons',
        body: (
          <>
            <p>The bottom of the sidebar shows who you are and your role. In the demo you can click it to become someone else (an ED nurse, an ED physician, or a nurse on one of the units) and see exactly what that person sees and can do.</p>
            <Terms
              items={[
                ['Moon or sun', 'Switches between the light and the dark theme.'],
                [<Key><PanelLeftClose /></Key>, 'Folds the sidebar to icons to give the page more room. Press it again to unfold. On a phone the sidebar opens from the menu button at the top left.'],
              ]}
            />
          </>
        ),
      },
    ],
  },
  {
    id: 'board',
    title: 'The Board',
    summary: 'Your work list: one row per patient with a handoff, with what needs attention.',
    topics: [
      {
        id: 'cards',
        title: 'The four boxes at the top',
        body: (
          <Terms
            items={[
              ['Boarding now', 'Patients with an admit decision who are still in the ED. Below the number: the median wait since the admit decision.'],
              ['Unstable', 'Boarding patients marked unstable. Look at these first.'],
              ['To sign', 'For ED staff: handoffs you have not signed yet. Below it: how many still have items to resolve.'],
              ['To acknowledge', 'For unit staff (shown instead of To sign): handoffs the ED has signed that are waiting for your read back.'],
              ['Arrived on unit', 'Patients who left the ED for a unit in the last 12 hours, so the receiving team can still review them.'],
            ]}
          />
        ),
      },
      {
        id: 'filters',
        title: 'Filters and search',
        body: (
          <Terms
            items={[
              ['All, Boarding, Arrived', 'Show everyone, only patients still in the ED, or only patients who already went up. The small number is how many are in each.'],
              ['Both hospitals, SGH, LKS', 'Show one hospital or both (ED staff only; unit staff already see only their unit).'],
              ['Search', 'Type a name, a medical record number or a unit, for example 6W.'],
            ]}
          />
        ),
      },
      {
        id: 'columns',
        title: 'How to read each column',
        body: (
          <Terms
            items={[
              ['Patient', 'Name, age, sex and medical record number. Click the row to open the handoff.'],
              [<SeverityBadge value="unstable" />, <>Illness severity, as in I-PASS: <b>stable</b> (expected to stay well), <b>watcher</b> (could get worse; keep a close eye), <b>unstable</b> (critical result not yet addressed, ICU level of care, or shock range vital signs). The app never shows a lower severity than these rules give.</>],
              ['Reason for visit', <>The chief complaint, with safety flags underneath: <Pill tone="danger">DNR/DNI</Pill> any code status other than full code, <Pill tone="warn">Contact</Pill> isolation, <Pill>2 allergies</Pill> allergies on file.</>],
              ['Destination', 'The unit and bed the patient is going to, then where they are now in the ED and the level of care (for example ICU, Telemetry, Med surg, Observation).'],
              ['Boarding', 'Time since the admit decision. The bar fills over 8 hours: blue under 4 hours, amber from 4 hours, red from 6 hours. For patients who already left, it says how long they waited.'],
              ['Handoffs', <>The status of the nurse and the physician handoff: <StateBadge state="draft" /> not signed, <StateBadge state="signed" /> signed by the ED, <StateBadge state="acknowledged" /> accepted by the unit, <StateBadge state="signed" changed /> changed after it was signed.</>],
              ['Completeness', <><span className="inline-flex items-center gap-1.5 align-middle"><ProgressRing value={0.58} size={18} stroke={3} blocking={4} />58%, 4 open</span> means 58% of the template's items are filled and 4 required items must be resolved before you can sign.</>],
              ['Last update', 'When the latest draft was written, and why (for example Bed assigned, Critical result, Note signed).'],
            ]}
          />
        ),
      },
      {
        id: 'todo',
        title: 'What to do on the Board',
        body: (
          <Steps
            items={[
              'Look at unstable patients and long boarding times first.',
              'ED staff: open each patient whose handoff is still Draft, then review and sign it.',
              'Unit staff: open each patient whose handoff is Signed, read it and accept it.',
              'Anything marked Updated has new information: open it and read what changed.',
            ]}
          />
        ),
      },
    ],
  },
  {
    id: 'handoff',
    title: 'A patient handoff',
    summary: 'What to look at, what needs your input, and what each part of the page means.',
    topics: [
      {
        id: 'first',
        title: 'What to look at first',
        body: (
          <>
            <p>The box at the top is the patient at a glance:</p>
            <Terms
              items={[
                ['Name and severity', 'Check you have the right patient, and how sick they are.'],
                ['Safety flags', 'Code status, allergies and isolation. Red means it changes how you care for the patient. "Not documented" means nobody has charted it yet.'],
                ['Now in, Going to', 'The ED bed now, and the unit, bed and level of care they are going to.'],
                ['Boarding', 'How long since the admit decision.'],
              ]}
            />
          </>
        ),
      },
      {
        id: 'tabs',
        title: 'The three tabs: SBAR, I-PASS, Joint Commission minimum',
        body: (
          <>
            <p>
              The same patient in three formats. <b>SBAR</b> is the nurse handoff, <b>I-PASS</b> the physician handoff, and the <b>Joint Commission minimum</b> a short format any clinician can use. Your role's tab opens first. The small badge on each tab shows whether that handoff is a draft, signed or acknowledged.
            </p>
            <p>Each handoff is divided into sections (for SBAR: Identify, Situation, Background, Assessment, Recommendation, Read back), and each section into items.</p>
          </>
        ),
      },
      {
        id: 'items',
        title: 'Two kinds of items: from the chart, and drafted by the model',
        body: (
          <Terms
            items={[
              ['From the chart', 'Allergies, code status, isolation, vital signs, labs, medications given, pending results, destination. These are copied straight from the record, so they cannot be edited here. If one is wrong, correct the chart; the next draft will pick it up.'],
              [<span className="inline-flex items-center gap-1"><Sparkles className="size-3.5" />Drafted by the model</span>, <>The working diagnosis, ED course, severity reasoning, mental status, action list, contingency plan and expected course. Read these carefully. Press <Key><Pencil />Edit</Key> to change the text; your version is kept and marked "Edited by" you.</>],
              [<span className="inline-grid size-[18px] place-items-center rounded bg-primary/10 text-[10px] font-semibold text-primary">1</span>, 'Each numbered chip is a source the text came from: a note, a lab, a medication, a set of vital signs. Hover to see what it is; click to read the record. The link chip next to chart items does the same.'],
              [<mark className="rounded-sm bg-flag/15 px-0.5 text-inherit underline decoration-flag decoration-dotted underline-offset-4">2.8</mark>, 'A highlighted number was not found in the sources the text cites. Often it is a total the model added up (for example fluids from two boluses). Check it before you sign.'],
              [<Pill tone="warn">Changed</Pill>, 'The item is different from the previous version (or from the version you signed). A thin amber line marks it on the left.'],
              [<span className="text-muted-foreground">Grey items</span>, 'Some items, such as lines and drains, fall risk and diet, are not sent by this demo hospital\'s systems. They are listed in grey and never block signing.'],
            ]}
          />
        ),
      },
      {
        id: 'severity',
        title: 'Illness severity and "Raised by rule"',
        body: (
          <>
            <p>
              The severity item shows the level, a box with the rule that applies, and the model's own assessment. If the chart meets a rule (for example an unaddressed critical lactate, ICU level of care, or a systolic pressure under 90), the app raises the severity to at least that level and says so: "Raised by rule". The model can make a patient sicker than the rule, never less sick.
            </p>
          </>
        ),
      },
      {
        id: 'resolve',
        title: 'Amber items: what must be resolved before signing',
        body: (
          <>
            <p>An amber message under an item means it is required and missing, or too old. You resolve it one of three ways:</p>
            <Terms
              items={[
                [<Key><Check />No known allergies confirmed</Key>, 'Tap the statement if it is true. This records that you checked. The options are a fixed list for each item.'],
                ['Stale vital signs', 'Vital signs must be recent for the patient\'s severity (60 minutes if stable, 30 if watcher, 15 if unstable). Recheck the patient, then tap "Rechecked at bedside, unchanged", or chart new vitals.'],
                ['Not documented (no buttons)', 'Press Edit on that item and write it.'],
              ]}
            />
            <p>Grey messages that say "does not block" are reminders only.</p>
          </>
        ),
      },
      {
        id: 'sign',
        title: 'The Sign off panel (ED staff)',
        body: (
          <Steps
            items={[
              'The ring and percentage show how complete the handoff is.',
              'The list shows every required item still open, with the same one tap buttons. When it is empty you see "Every required item is documented or attested."',
              'Type the number the receiving clinician can call you on.',
              <>Press Sign. Your name, callback and time are added to the handoff, and the unit can now accept it.</>,
              'If a newer draft arrives after you signed, the panel says so. Read what changed and sign again.',
            ]}
          />
        ),
      },
      {
        id: 'ack',
        title: 'The Acknowledge panel (unit staff)',
        body: (
          <>
            <Steps
              items={[
                'Before the ED signs, you can read the draft, but it may still change.',
                'Once it is signed, read it, especially severity, pending results, the action list and the contingency plan.',
                'Write a short read back in your own words: who the patient is, the main risk, and what you will do first. Add any questions for the sender.',
                'Press Accept handoff. If it changes later, it is flagged Updated and you accept it again.',
              ]}
            />
            <Note>Example read back: "72 F with sepsis from a urinary source, watcher; lactate coming down. I will repeat vitals on arrival and follow the pending blood cultures."</Note>
          </>
        ),
      },
      {
        id: 'context',
        title: 'What changed, vital signs, lab trends, history',
        body: (
          <Terms
            items={[
              ['What changed', 'Compared with the previous version (or the one you signed): items updated from the chart, and text the model rewrote, with the old text crossed out. Click an item to jump to it.'],
              ['Vital signs chart', 'Every set taken in the ED. Top: heart rate and blood pressure; bottom: oxygen saturation and breathing rate. Move the mouse over the chart to see the values at that time; click a name above the chart to hide or show that line.'],
              ['Lab trends', 'Each result that was ever abnormal: the latest value (red if critical, amber if abnormal), a small trend line, and first to latest.'],
              ['History', 'Every version and why it was written, and every edit, attestation, signature and acceptance, newest first.'],
            ]}
          />
        ),
      },
      {
        id: 'checklist',
        title: 'Checklist',
        body: (
          <Terms
            items={[
              ['ED, before signing', 'Right patient. Severity makes sense. Model text is accurate (edit if not). Highlighted numbers checked. No amber items left. Callback added.'],
              ['Unit, before accepting', 'Severity and contingency plan understood. Pending results and who owns them noted. Action list read. Read back written. Questions asked.'],
            ]}
          />
        ),
      },
    ],
  },
  {
    id: 'analytics',
    title: 'Analytics',
    summary: 'How the week has gone, up to the time on the clock.',
    topics: [
      {
        id: 'cards',
        title: 'The four boxes',
        body: (
          <Terms
            items={[
              ['Admitted from the ED', 'Patients who went up to an inpatient or observation bed so far.'],
              ['Peak boarding', 'The most patients waiting for a bed at the same time, and when.'],
              ['Median completeness', 'How complete the drafts were at transfer before anyone edited them. The rest is filled by clinicians.'],
              ['Model calls', 'How many drafts were written, and how many were updates after the patient had left the ED (addenda).'],
            ]}
          />
        ),
      },
      {
        id: 'census',
        title: 'ED census and boarding',
        body: <p>The blue area is how many patients were in the ED each hour; the orange line is how many of them were waiting for a bed. Move the mouse over the chart to read the numbers at a given hour; click a name above the chart to hide it. Drag to scroll, or use the mouse wheel to zoom.</p>,
      },
      {
        id: 'units',
        title: 'Boarding time by destination',
        body: <p>For each unit, the dark bar is the median wait from admit decision to leaving the ED (half waited less, half more); the light bar is the 90th percentile (1 patient in 10 waited longer). Hover a bar for the exact time and the number of patients.</p>,
      },
      {
        id: 'blocked',
        title: 'What blocked sign off at transfer',
        body: <p>The items the model's drafts most often could not fill from the chart, so a clinician had to confirm or write them. Long bars point to documentation that is often missing in the ED.</p>,
      },
      {
        id: 'quality',
        title: 'Completeness and severity at transfer',
        body: <p>Completeness: how many handoffs reached each level before clinician edits. Severity: how many patients were stable, watcher or unstable when they left the ED.</p>,
      },
    ],
  },
  {
    id: 'templates',
    title: 'Templates',
    summary: 'What each handoff contains, and which items must be filled.',
    topics: [
      {
        id: 'read',
        title: 'How to read a template',
        body: (
          <Terms
            items={[
              ['Sections', 'The headings of the handoff, in order (for example Situation, Background).'],
              ['From the chart', 'Copied from the record; cannot be edited.'],
              ['Model drafted', 'Written by the model; you review and can edit.'],
              ['In the app', 'Filled when you sign or accept (sender, callback, read back).'],
              [<Pill tone="danger">Blocks sign off</Pill>, 'Must be documented or confirmed before signing.'],
              [<Pill tone="warn">Must be recent</Pill>, 'Must be recent enough for the patient\'s severity; the minutes are listed.'],
              [<Pill>Warning</Pill>, 'Counts toward completeness but never blocks.'],
              [<Pill tone="info">Checked at sign</Pill>, 'Completed by the sign off or the acceptance itself.'],
            ]}
          />
        ),
      },
      {
        id: 'change',
        title: 'Changing a template',
        body: <p>Your informatics team can rename sections, move items and change which items block signing. They edit the template files in the app's repository; the next drafts follow the new template.</p>,
      },
    ],
  },
  {
    id: 'glossary',
    title: 'Words used in the app',
    summary: 'Short definitions.',
    topics: [
      {
        id: 'terms',
        title: 'Glossary',
        body: (
          <Terms
            items={[
              ['Admit decision', 'The moment the ED decides the patient needs a bed. The handoff is drafted then.'],
              ['Boarding', 'Waiting in the ED after the admit decision.'],
              ['ESI', 'Emergency Severity Index, the triage level from 1 (most urgent) to 5.'],
              ['MRN', 'Medical record number.'],
              ['Level of care', 'ICU, step down, telemetry, med surg or observation.'],
              ['SBAR', 'Situation, Background, Assessment, Recommendation: the nurse handoff format.'],
              ['I-PASS', 'Illness severity, Patient summary, Action list, Situation awareness and contingency, Synthesis by the receiver: the physician format.'],
              ['Attest', 'Confirm a statement from a fixed list, such as "No known allergies confirmed".'],
              ['Read back', 'The receiver repeats the key points in their own words to show they understood.'],
              ['Version', 'Each time the chart changes in a way that matters, a new draft is written. The newest is shown.'],
              ['Addendum', 'A new version after the patient left the ED, for an ED note signed late or a critical result from a test done in the ED.'],
              ['Source', 'The chart record a statement came from.'],
            ]}
          />
        ),
      },
    ],
  },
];

export function findTopic(id: string): { section: GuideSection; topics: Topic[] } | null {
  const [s, t] = id.split('.');
  const section = GUIDE.find((x) => x.id === s);
  if (!section) return null;
  const topics = t ? section.topics.filter((x) => x.id === t) : section.topics;
  return topics.length ? { section, topics } : null;
}
