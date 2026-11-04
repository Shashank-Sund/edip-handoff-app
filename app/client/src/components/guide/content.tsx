/**
 * The user guide, written for nurses and physicians. One source for the How to use page and for the ? buttons,
 * which open one topic (or one whole section) in a side panel. Topic ids are "<section>" or "<section>.<topic>",
 * and each topic has the anchor #<section>-<topic> on the guide page.
 *
 * The page reads top to bottom: orientation, a first walkthrough, tasks by role, common questions, then a page
 * by page reference, a glossary and the About section (in pages/Guide.tsx, because it needs the app config).
 */
import type { ReactNode } from 'react';
import {
  CalendarClock,
  Check,
  CheckCheck,
  ChevronLeft,
  ChevronRight,
  ChevronsUpDown,
  CircleHelp,
  Lock,
  Menu,
  Moon,
  PanelLeftClose,
  Pencil,
  PenLine,
  Play,
  RotateCcw,
  Sun,
} from 'lucide-react';
import { Pill, SeverityBadge, StateBadge } from '@/components/common/badges';
import { Key, Primary, Tag, AmberNote, GreyNote, ModelMark, Chip, Highlight, Ring, GuideLink, PageLink, Terms, Checks, Note, SeeDo, ColorRow, WhoDoesWhat } from './cues';

export interface Topic {
  id: string;
  title: string;
  body: ReactNode;
}

/** How the guide page lays a section out. */
export type SectionKind = 'intro' | 'steps' | 'roles' | 'questions' | 'reference' | 'glossary';

export interface GuideSection {
  id: string;
  title: string;
  /** Shorter label for the table of contents. */
  nav?: string;
  summary: string;
  kind: SectionKind;
  topics: Topic[];
}

// ------------------------------------------------------------------------------------ the guide

export const GUIDE: GuideSection[] = [
  {
    id: 'start',
    title: 'Start here',
    summary: 'What the app does for you, and who does what.',
    kind: 'intro',
    topics: [
      {
        id: 'what',
        title: 'What this app does for you',
        body: (
          <>
            <p>
              When the ED decides to admit a patient, the app writes a draft handoff from the chart. The ED team checks it, fixes what is wrong and signs it. The receiving team reads it and accepts it with a short read back, so nothing important is lost on the way to the unit.
            </p>
            <p className="text-muted-foreground">The app does not replace talking to each other. It makes sure nothing is missing before you do.</p>
          </>
        ),
      },
      {
        id: 'who',
        title: 'Who does what',
        body: (
          <>
            <WhoDoesWhat />
            <p className="text-muted-foreground">
              There is also a short third format, the Joint Commission minimum. Either ED clinician can sign it, and either receiver can accept it.
            </p>
          </>
        ),
      },
      {
        id: 'help',
        title: 'Help on every page',
        body: (
          <p>
            Look for the <Key label="question mark"><CircleHelp /></Key> button next to a title or a panel. It opens a short explanation of that part of the screen without leaving the page, with a link back to this guide. A label with a <span className="underline decoration-dotted underline-offset-[3px]">dotted underline</span>, and the small <CircleHelp className="inline size-3.5 align-middle" aria-hidden /> next to each column of the board, explain themselves when you point at them, tab to them or tap them.
          </p>
        ),
      },
    ],
  },
  {
    id: 'first',
    title: 'Your first five minutes',
    nav: 'First five minutes',
    summary: 'Follow these steps in the demo once. You will see a whole handoff, from draft to accepted.',
    kind: 'steps',
    topics: [
      {
        id: 'role',
        title: 'Pick your role',
        body: (
          <SeeDo
            see={<>At the bottom of the sidebar, your name and role, for example <Key>Jordan Reyes, RN · ED nurse<ChevronsUpDown /></Key>.</>}
            act={<>Click it to choose who you are. For this walkthrough, start as the <b>ED nurse</b>. On a phone, open the menu <Key label="menu"><Menu /></Key> at the top left first.</>}
          />
        ),
      },
      {
        id: 'board',
        title: 'Read the board',
        body: (
          <SeeDo
            see={<>Four boxes at the top (Boarding now, Unstable, To sign, Arrived on unit), then one row per patient. Patients still in the ED come first, the sickest at the top.</>}
            act={<>Glance at the boxes to see how busy it is. Then look down the <b>Severity</b> and <b>Boarding</b> columns.</>}
          />
        ),
      },
      {
        id: 'open',
        title: 'Open the sickest boarding patient',
        body: (
          <SeeDo
            see={<>The top row, marked <SeverityBadge value="unstable" />, with both handoffs at <StateBadge state="draft" />.</>}
            act={<>Click anywhere on the row. The patient's handoff opens on the SBAR tab, because you are a nurse.</>}
          />
        ),
      },
      {
        id: 'review',
        title: 'Review the draft',
        body: (
          <SeeDo
            see={<>The patient box at the top (name, severity, code status, allergies, bed), then the handoff in sections. Some items say <ModelMark />.</>}
            act={<>Check it is the right patient. Read every model item. Hover a numbered chip <Chip n={1} /> to see its source. If the text is wrong, press <Key><Pencil />Edit</Key> and fix it.</>}
          />
        ),
      },
      {
        id: 'resolve',
        title: 'Resolve the flagged items',
        body: (
          <SeeDo
            see={<>The <b>Sign off</b> panel lists what still blocks signing, each marked <Tag tone="danger">Required</Tag> or <Tag tone="warn">Stale</Tag>. The same items show an amber message in the handoff.</>}
            act={<>For each one, check the patient, then tap what is true, for example <Key><Check />No isolation needed</Key>, <Key><Check />Droplet</Key> or <Key><Check />Rechecked at bedside, unchanged</Key>. For an allergy the chart does not have, tap <Key><Pencil />Add an allergy</Key> and type it.</>}
          />
        ),
      },
      {
        id: 'sign',
        title: 'Sign',
        body: (
          <SeeDo
            see={<>The panel now says "Every required item is documented or attested." and the ring turns green.</>}
            act={<>Type the number the unit can call you on, then press <Primary><PenLine />Sign</Primary>. The SBAR tab changes to <StateBadge state="signed" />.</>}
          />
        ),
      },
      {
        id: 'switch',
        title: 'Switch to the receiving unit',
        body: (
          <SeeDo
            see={<>The patient box shows where the patient is going, for example "Going to SGH 7E".</>}
            act={<>Open the role menu at the bottom of the sidebar. Under <b>Receiving units</b>, pick that unit. The board now reads "Incoming to SGH 7E" and shows only that unit's patients.</>}
          />
        ),
      },
      {
        id: 'accept',
        title: 'Read back and accept',
        body: (
          <SeeDo
            see={<>The <b>To acknowledge</b> box counts your patient. On the handoff page, an <b>Acknowledge</b> panel with a box for your read back.</>}
            act={<>Read the handoff. Write a short read back in your own words, add any questions, then press <Primary><CheckCheck />Accept handoff</Primary>. The tab changes to <StateBadge state="acknowledged" />.</>}
          />
        ),
      },
      {
        id: 'clock',
        title: 'Move the clock and watch what changes',
        body: (
          <SeeDo
            see={<>The date and time at the top right, for example <Key><CalendarClock />Wed, Oct 28 16:00</Key>. Every page shows the hospital at that moment.</>}
            act={<>Press <Key label="forward"><ChevronRight /></Key> to go forward one hour, or <Key label="play"><Play /></Key> to play. Boarding times grow, new drafts may arrive and show <StateBadge state="changed" /> on a signed handoff, and patients leave for their unit.</>}
          />
        ),
      },
    ],
  },
  {
    id: 'roles',
    title: 'What to do in your role',
    nav: 'By role',
    summary: 'The same app, four jobs. Find yours.',
    kind: 'roles',
    topics: [
      {
        id: 'ed-nurse',
        title: 'If you are the ED nurse',
        body: (
          <>
            <p>You see every patient waiting for a bed, at both hospitals. Your tab is <b>SBAR</b>. The <b>To sign</b> box counts nurse handoffs in Draft or Changed after signing.</p>
            <p className="font-medium">Before you sign, check:</p>
            <Checks
              items={[
                'Right patient, and the severity makes sense.',
                'The model text is accurate. Edit it if not.',
                'Highlighted numbers are checked.',
                'Vital signs are recent enough for the severity.',
                'No Required or Stale items left, and your callback number is in.',
              ]}
            />
            <p className="text-muted-foreground">If a signed handoff later shows <StateBadge state="changed" />, something changed after your signature: your own edit or attestation, or a newer draft with different content. Read what changed and press Sign again; the receiver cannot accept until you do.</p>
          </>
        ),
      },
      {
        id: 'ed-physician',
        title: 'If you are the ED physician',
        body: (
          <>
            <p>You see every patient waiting for a bed. Your tab is <b>I-PASS</b>. You sign the same way as the nurse.</p>
            <p className="font-medium">Pay extra attention to:</p>
            <Checks
              items={[
                'Working diagnosis and illness severity.',
                'The action list: each task has an owner and a time.',
                'The contingency plan: what could go wrong, and what to do.',
                'Pending results, and who will follow them up.',
              ]}
            />
            <p className="text-muted-foreground">The admitting team will act on these, so make them right before you sign.</p>
          </>
        ),
      },
      {
        id: 'unit-nurse',
        title: 'If you are the receiving nurse',
        body: (
          <>
            <p>
              You see only patients coming to your unit, and those who arrived in the last 12 hours. The board is titled "Incoming to" your unit. The <b>To acknowledge</b> box counts handoffs the ED has signed for you.
            </p>
            <Checks
              items={[
                <>You can read a <StateBadge state="draft" /> early, but it may still change.</>,
                <>Once it is <StateBadge state="signed" />, read it, write your read back and press Accept handoff.</>,
                'Note the severity, the pending results and the contingency plan.',
                'Use the callback number on the signature if you have questions.',
              ]}
            />
            <p className="text-muted-foreground">If it shows <StateBadge state="changed" />, wait: the ED has to sign it again before you can accept. If the ED signs again after you accepted, the panel says Acknowledge the update; read what changed and accept again.</p>
          </>
        ),
      },
      {
        id: 'admitting',
        title: 'If you are the admitting physician',
        body: (
          <>
            <p>You see every admitted patient, at both hospitals. Your tab is <b>I-PASS</b>. You accept the physician handoff with a read back, the same way as the receiving nurse.</p>
            <Checks
              items={[
                'Read the action list and the contingency plan first.',
                'Your read back is the "S" of I-PASS: the synthesis by the receiver.',
                'Write questions for the ED physician in the box under the read back.',
              ]}
            />
          </>
        ),
      },
    ],
  },
  {
    id: 'questions',
    title: 'Common questions',
    summary: 'Short answers to what people ask most.',
    kind: 'questions',
    topics: [
      {
        id: 'colors',
        title: 'What does a red or amber item mean?',
        body: (
          <>
            <p>The colors mean the same thing on every page.</p>
            <div className="divide-y rounded-lg border">
              <ColorRow swatch="bg-sev-unstable" title="Red: act now, or it changes how you care for the patient">
                <SeverityBadge value="unstable" />
                <Pill tone="danger">DNR/DNI</Pill>
                <Tag tone="danger">Required</Tag>
                <StateBadge state="transferred_unsigned" />
                <span>a critical lab, boarding 6 hours or more</span>
              </ColorRow>
              <ColorRow swatch="bg-sev-watcher" title="Amber: needs your attention before you go on">
                <SeverityBadge value="watcher" />
                <AmberNote>Isolation: not documented</AmberNote>
                <Pill tone="warn">Entered by you, not yet in the chart</Pill>
                <Tag tone="warn">Stale</Tag>
                <Pill tone="warn">Changed</Pill>
                <StateBadge state="changed" />
                <Highlight>2.8</Highlight>
                <span>an abnormal lab, boarding 4 hours or more</span>
              </ColorRow>
              <ColorRow swatch="bg-state-signed" title="Blue: signed by the ED">
                <StateBadge state="signed" />
              </ColorRow>
              <ColorRow swatch="bg-sev-stable" title="Green: done, or nothing to worry about">
                <SeverityBadge value="stable" />
                <StateBadge state="acknowledged" />
                <Ring value={1} label="nothing blocks" />
              </ColorRow>
              <ColorRow swatch="bg-muted-foreground" title="Grey: not signed yet, or only a reminder">
                <StateBadge state="draft" />
                <GreyNote>Consults: not documented (does not block)</GreyNote>
              </ColorRow>
            </div>
          </>
        ),
      },
      {
        id: 'cant-sign',
        title: 'Why can I not sign yet?',
        body: (
          <>
            <p>The <Primary><PenLine />Sign</Primary> button stays pale until all of these are true:</p>
            <Checks
              items={[
                <>Nothing is left in the Sign off list. Each open item says <Tag tone="danger">Required</Tag> or <Tag tone="warn">Stale</Tag>; <GuideLink to="handoff-resolve">see how to resolve them</GuideLink>.</>,
                'You typed a callback number the receiver can dial: at least 3 digits, or an extension like x4471.',
                'It is your handoff. ED nurses sign SBAR, ED physicians sign I-PASS, and either signs the Joint Commission minimum. On another tab the panel says "Your role does not sign this template."',
                <>The patient is still in the ED. Once they leave, the panel shows <Lock className="inline size-3.5 align-middle" aria-hidden /> "The patient left the ED; this handoff is final." The exceptions: an addendum, and a handoff that was never signed (<StateBadge state="transferred_unsigned" />) or was signed on an older version. Those you can still complete and sign.</>,
                <>The clock is not earlier than something already recorded on this handoff. If it is, the app says "You are viewing an earlier time; move the clock to now to act."</>,
              ]}
            />
            <p className="text-muted-foreground">Staff on the receiving unit never sign. They accept.</p>
          </>
        ),
      },
      {
        id: 'cant-accept',
        title: 'Why can I not accept yet?',
        body: (
          <Checks
            items={[
              'The ED has to sign first. Until then the panel says "Waiting for the ED clinician to sign."',
              'The handoff did not change after it was signed. If it did, the panel says "Waiting for the ED to sign again." You can accept once the ED signs the new text.',
              <>Your read back needs at least a short sentence: three words or more and fifteen letters. <Primary><CheckCheck />Accept handoff</Primary> stays pale until then, and the line under it says what is missing.</>,
              'It is your handoff. Receiving nurses accept SBAR, admitting physicians accept I-PASS, and either accepts the Joint Commission minimum.',
            ]}
          />
        ),
      },
      {
        id: 'model',
        title: 'What did the model write, and what came from the chart?',
        body: (
          <>
            <Terms
              items={[
                ['From the chart', 'Allergies, code status, isolation, vital signs, labs, imaging, medications given, pending results and destination. These are copied from the record as they are, so they have no Edit button. If one is wrong, correct the chart; the next draft picks it up.'],
                [<ModelMark key="m" />, <>Working diagnosis, illness severity, ED course, mental status, action list, contingency plan and anticipated disposition. Read these with care. Press <Key><Pencil />Edit</Key> to change one (severity follows its own rules and has no Edit).</>],
              ]}
            />
            <p>
              Every model sentence names its sources as numbered chips <Chip n={1} /> <Chip n={2} />. Hover one to see the record, click to read it. A <Highlight>highlighted number</Highlight> was not found in those sources, often a total the model added up. Check it before you sign.
            </p>
            <p className="text-muted-foreground">
              Before anyone sees a draft, a checker removes references to records that do not exist and highlights the numbers it cannot find. When you edit an item, it says <span className="inline-flex items-center gap-1 align-middle text-xs"><Pencil className="size-3" aria-hidden />Edited by</span> your name, and your text is kept in later versions. An edit cannot be empty: to go back to the model's text, press <Key><RotateCcw />Restore</Key> on the item.
            </p>
          </>
        ),
      },
      {
        id: 'changes',
        title: 'What changed since the last version?',
        body: (
          <>
            <p>
              When something important happens (a new result, a bed assigned, a note signed), the app writes a new version of the draft. Items that differ are marked <Pill tone="warn">Changed</Pill> with a thin amber line on their left.
            </p>
            <p>
              The <b>What changed</b> panel lists them in two groups: <b>Updated from the chart</b>, and <b>Redrafted by the model</b> with the old text <span className="line-through">crossed out</span>. Click an item to jump to it. Once a signed handoff has changed, it compares the text you signed, your edits included, with the text now.
            </p>
            <p className="text-muted-foreground">
              Any edit or attestation after signing, or a newer version whose clinical content differs, puts a signed handoff in <StateBadge state="changed" />: the ED signs again, then the receiver accepts. The signature stays in History. A new version that only records the move to the unit, or the same code status charted again, does not count as a change.
            </p>
          </>
        ),
      },
      {
        id: 'gone',
        title: 'Where did my signature go?',
        body: (
          <p>
            Probably the clock. Every page shows the hospital at the time on the clock, and your actions are recorded at that time too. Move the clock back to before you signed and the signature is not there yet; move it forward again and it returns. See <GuideLink to="clock-actions">your actions and the clock</GuideLink>.
          </p>
        ),
      },
    ],
  },

  // ---------------------------------------------------------------------------------- page by page reference
  {
    id: 'board',
    title: 'The Board',
    summary: 'Your work list: one row per patient with a handoff.',
    kind: 'reference',
    topics: [
      {
        id: 'cards',
        title: 'The four boxes at the top',
        body: (
          <Terms
            items={[
              ['Boarding now', 'Patients with an admit decision who are still in the ED. Below the number: the median wait since the admit decision. The read only viewer sees this, Unstable and Arrived, without the To sign or To acknowledge box.'],
              ['Unstable', 'Boarding patients marked unstable, by the rule floor or by the model. Look at these first.'],
              ['To sign', 'ED staff: handoffs for your role in Draft or Changed after signing. Below it: how many of those still have items to resolve. When patients went up to the unit before anyone signed, a red line such as "1 left without sign off" shows them: click it to list them.'],
              ['To acknowledge', 'Unit staff, in place of To sign: handoffs the ED has signed that wait for your read back.'],
              ['Arrived on unit', 'Patients who went up to a unit in the last 12 hours, so the receiving team can still review them. Canceled transfers are not counted.'],
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
              [<Key key="k">All · Boarding · Arrived</Key>, 'Everyone, only patients still in the ED, or only patients who already went up. The small number is how many.'],
              [<Key key="k">Both hospitals · SGH · LKS</Key>, 'One hospital or both. Unit staff do not see this, because they see only their unit.'],
              [<Pill key="p" tone="danger">Left without sign off</Pill>, 'Shown after you click the red line under To sign: only arrived patients whose handoff for your role was never signed. You can still sign them. Press its x to clear it.'],
              ['Search', 'Type a name, a medical record number or a unit, for example 6W.'],
            ]}
          />
        ),
      },
      {
        id: 'columns',
        title: 'How to read each column',
        body: (
          <>
            <p>Patients still in the ED come first, the sickest at the top. Click a row to open the handoff. On a phone, each patient is a card with the same information. Point at the small <CircleHelp className="inline size-3.5 align-middle" aria-hidden /> next to a column title for a short reminder of what it shows.</p>
            <Terms
              items={[
                ['Patient', 'Name, age, sex and medical record number (MRN).'],
                ['Severity', <><SeverityBadge value="stable" /> <SeverityBadge value="watcher" /> <SeverityBadge value="unstable" /> See <GuideLink to="handoff-severity">illness severity</GuideLink>.</>],
                ['Reason for visit', <>The chief complaint, with safety flags underneath: <Pill tone="danger">DNR/DNI</Pill> any code status other than full code, <Pill tone="warn">Contact</Pill> isolation, <Pill>2 allergies</Pill> allergies on file.</>],
                ['Destination', 'The unit and bed the patient is going to, where they are now in the ED, and the level of care.'],
                ['Boarding', 'Time since the admit decision. The bar fills over 8 hours: blue under 4 hours, amber from 4, red from 6. For patients who already left: how long they waited.'],
                ['Handoffs', <>The nurse and physician handoff: <StateBadge state="draft" /> <StateBadge state="signed" /> <StateBadge state="acknowledged" />, <StateBadge state="changed" /> if it changed after it was signed, and <StateBadge state="transferred_unsigned" /> if the patient left the ED before anyone signed.</>],
                ['Completeness', <><Ring value={0.58} blocking={4} label="58%, 4 open" /> For the template of your role, named in the column title (SBAR for nurses, I-PASS for physicians): 58% of its checks are met or resolved, and 4 required items must be resolved before signing.</>],
                ['Last update', 'When the latest draft was written, and why: Admit decision, Bed assigned, Note signed, Critical result and so on.'],
              ]}
            />
          </>
        ),
      },
    ],
  },
  {
    id: 'handoff',
    title: 'The handoff page',
    nav: 'Handoff page',
    summary: 'One patient: the handoff on the left, sign off and context on the right.',
    kind: 'reference',
    topics: [
      {
        id: 'top',
        title: 'The patient box',
        body: (
          <Terms
            items={[
              ['Name and severity', 'Check you have the right patient, and how sick they are.'],
              ['Safety flags', <>Code status, allergies and isolation from the chart, for example <Key>Full Code</Key> <Pill tone="danger">Shellfish</Pill>. Red changes how you care for the patient. "No allergies documented" means the chart has none on file, which is not the same as no known allergies.</>],
              ['Now in, Going to', 'The ED bed now, and the unit, bed and level of care they are going to.'],
              ['Boarding', 'How long since the admit decision.'],
            ]}
          />
        ),
      },
      {
        id: 'tabs',
        title: 'The three tabs: SBAR, I-PASS, Joint Commission minimum',
        body: (
          <>
            <p>
              The same patient in three formats. <b>SBAR</b> is the nurse handoff: the ED nurse signs it, the receiving nurse accepts it. <b>I-PASS</b> is the physician handoff: the ED physician signs it, the admitting physician accepts it. The <b>Joint Commission minimum</b> is a short format with what every handoff needs: either ED clinician signs it, either receiver accepts it. Point at a tab for a reminder. Your role's tab opens first. The small icon on each tab shows its status: <StateBadge state="draft" compact /> draft, <StateBadge state="signed" compact /> signed, <StateBadge state="acknowledged" compact /> accepted.
            </p>
            <p className="text-muted-foreground">Next to the tabs: the version number, when it was drafted and why. Each handoff is split into sections (for SBAR: Identify, Situation, Background, Assessment, Recommendation, Read back).</p>
          </>
        ),
      },
      {
        id: 'items',
        title: 'Chips, highlights and other marks',
        body: (
          <Terms
            items={[
              [<ModelMark key="m" />, <>Written by the model. Read it, and press <Key><Pencil />Edit</Key> if it is wrong. Items without this mark come straight from the chart.</>],
              [<Chip key="c" n={1} />, 'A source the text came from: a note, a lab, a medication, a set of vital signs. Hover to see it; click to read the record. The small link chip next to chart items does the same.'],
              [<Highlight key="h">2.8</Highlight>, 'A number not found in the sources the text cites. Often a total the model added up. Check it before you sign.'],
              [<Pill key="p" tone="warn">Changed</Pill>, 'Different from the previous version, or from the one you signed. A thin amber line marks it on the left.'],
              ['Grey text', '"Not in the reference feeds": this demo hospital does not send lines and drains, fall risk or diet. These never block signing.'],
            ]}
          />
        ),
      },
      {
        id: 'severity',
        title: 'Illness severity and the rule floor',
        body: (
          <>
            <p>
              Severity is <SeverityBadge value="stable" />, <SeverityBadge value="watcher" /> or <SeverityBadge value="unstable" />, as in I-PASS. Rules set the lowest level the chart allows, the <b>rule floor</b>. The model can make a patient sicker than the floor, never less sick.
            </p>
            <Terms
              items={[
                [<SeverityBadge key="u" value="unstable" />, 'A critical result not yet addressed, ICU level of care, or shock range vital signs: systolic under 90, oxygen saturation under 90, heart rate over 130, breathing rate over 30, or GCS under 13.'],
                [<SeverityBadge key="w" value="watcher" />, 'ESI 1 or 2, step down level of care, any critical result during the visit, abnormal latest vital signs, or a preliminary result still pending.'],
                [<SeverityBadge key="s" value="stable" />, 'None of the above.'],
              ]}
            />
            <p className="text-muted-foreground">
              Under the level, a box names the rule that applies. It says "Rule floor" when the model agreed, and "Raised by rule" when the model had judged the patient less sick. The model's own reasoning follows.
            </p>
          </>
        ),
      },
      {
        id: 'resolve',
        title: 'Resolving Required and Stale items',
        body: (
          <>
            <p>An amber message under an item, such as <AmberNote>Isolation: not documented</AmberNote>, means it blocks signing. There are three kinds:</p>
            <Terms
              items={[
                ['Not documented, with choices', <>Tap what is true. The first choice confirms there is nothing to add, for example <Key><Check />No known allergies confirmed</Key>. For isolation, code status and allergies the other choices enter what you found: <Key><Check />Droplet</Key>, <Key><Check />DNR/DNI</Key>, or <Key><Pencil />Other</Key> and <Key><Pencil />Add an allergy</Key>, which ask you to type it. The choices are a fixed list for each item. A wrong choice can be fixed with <b>Change</b> under the item; after signing, that means signing again. <GuideLink to="handoff-entered">Values you enter</GuideLink> explains what the receiver sees.</>],
                [<Tag key="t" tone="warn">Stale</Tag>, <>Vital signs must be recent for the severity. The limits depend on the template: SBAR 60, 30 and 15 minutes for a stable, watcher and unstable patient; I-PASS 120, 60 and 30 minutes. The <PageLink to="/templates">Templates page</PageLink> lists them. Recheck the patient, then tap <Key><Check />Rechecked at bedside, unchanged</Key>, or chart new vital signs. A recheck counts for the same limit: after that the item is stale again and the panel says the recheck expired.</>],
                ['Not documented, no statement', <>For a model item, press <Key><Pencil />Edit</Key> and write it. For a chart item, document it in the chart; the next draft picks it up.</>],
              ]}
            />
            <p className="text-muted-foreground">
              Grey messages that end in "(does not block)" are reminders. They lower completeness but never stop you signing.
            </p>
          </>
        ),
      },
      {
        id: 'entered',
        title: 'Values you enter',
        body: (
          <>
            <p>
              When the chart has no isolation, code status or allergy and you enter one in the sign off, the item shows it with <Pill tone="warn">Entered by Jordan Reyes, RN, not yet in the chart</Pill>. The receiving team sees the same tag, so they know it came from you, not from the record.
            </p>
            <p className="text-muted-foreground">
              Enter it in the chart as well. When a newer draft has the value from the chart, the item shows the chart value, and what you entered stays in History. Like any attestation, entering a value after you signed means you sign again.
            </p>
          </>
        ),
      },
      {
        id: 'sign',
        title: 'The Sign off panel (ED staff)',
        body: (
          <>
            <p>On a wide screen it sits at the top of the right column, which scrolls on its own, so it stays in reach however long the handoff is. On a smaller screen it comes after the handoff; a bar at the bottom of the screen, such as "Sign off: 2 items to resolve", says where it stands and takes you there.</p>
            <Terms
              items={[
                [<Ring key="r" value={0.68} blocking={2} label="68% complete" />, 'How complete the handoff is, and how many items still block signing.'],
                ['The list', <>Every open item, marked <Tag tone="danger">Required</Tag> or <Tag tone="warn">Stale</Tag>, with the same choices as on the item. Click an item's name to jump to it.</>],
                ['Your callback number', 'The number the receiving clinician can call you on.'],
                [<Primary key="s"><PenLine />Sign</Primary>, 'Adds your name, callback and the time. The receiving unit can now accept it.'],
              ]}
            />
            <p className="text-muted-foreground">
              If anything changes after you sign (your edit or attestation, or a newer draft with different content), the panel says "Changed after signing, sign again" and why. Read what changed and press <Primary><PenLine />Sign again</Primary>. If the patient left the ED before anyone signed, it says "Transferred without sign off", and you can still sign. Unit staff see this panel as <b>Handoff status</b>, without the Sign button.
            </p>
          </>
        ),
      },
      {
        id: 'ack',
        title: 'The Acknowledge panel (unit staff)',
        body: (
          <>
            <Checks
              items={[
                'Before the ED signs, the panel says "Waiting for the ED clinician to sign." You can read the draft, but it may still change. If it changed after signing, it says "Waiting for the ED to sign again."',
                'Once it is signed, read it, especially severity, pending results, the action list and the contingency plan.',
                'Write a short read back in your own words, at least a sentence: who the patient is, the main risk, and what you will do first. Add any questions for the sender.',
                <>Press <Primary><CheckCheck />Accept handoff</Primary>. If the handoff changes later, the ED signs again and the panel becomes <b>Acknowledge the update</b>; you accept it again.</>,
              ]}
            />
            <Note>Example read back: "72 F with sepsis from a urinary source, watcher; lactate trending down. I will repeat vitals on arrival and follow the pending blood cultures."</Note>
          </>
        ),
      },
      {
        id: 'context',
        title: 'What changed, vital signs, lab trends and history',
        body: (
          <Terms
            items={[
              ['What changed', <>Compared with the previous version, or, once a signed handoff changed, with the text that was signed (clinician edits included): items <b>Updated from the chart</b>, and text that changed with the old text crossed out. Click an item to jump to it.</>],
              ['Vital signs', 'Every set taken in the ED. Top: heart rate and blood pressure. Bottom: oxygen saturation and breathing rate. Point at the chart to read the values at that time; click a name above it to hide or show that line.'],
              ['Lab trends', 'Each result that was ever abnormal: the latest value (red if critical, amber if abnormal), a small trend line, and first to latest.'],
              ['History', 'Every version and why it was written, and every edit, restore, attestation, signature and acceptance with the name of the item, newest first.'],
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
    kind: 'reference',
    topics: [
      {
        id: 'cards',
        title: 'The four boxes',
        body: (
          <Terms
            items={[
              ['Admitted from the ED', 'Patients who went up to an inpatient or observation bed so far, at both hospitals and all units; below it, how many were signed before transfer and how many were transferred without sign off.'],
              ['Peak boarding', 'The most patients waiting for a bed at the same time, and when.'],
              ['Median completeness', 'How complete the model drafts were at transfer, before anyone edited them. Clinicians fill the rest.'],
              ['Model calls', 'How many drafts were written, and how many were addenda after the patient left the ED.'],
            ]}
          />
        ),
      },
      {
        id: 'census',
        title: 'ED census and boarding',
        body: <p>The blue area is how many patients were in the ED each hour; the orange line is how many of them were boarding. Point at the chart to read the numbers for an hour, and click a name above it to hide that line. Drag to scroll, or use the mouse wheel to zoom.</p>,
      },
      {
        id: 'units',
        title: 'Boarding time by destination',
        body: <p>For each unit, the dark bar is the median wait from admit decision to leaving the ED: half waited less, half waited more. The light bar is the 90th percentile: 1 patient in 10 waited longer. Point at a bar for the exact time and number of patients.</p>,
      },
      {
        id: 'blocked',
        title: 'What blocked sign off at transfer',
        body: <p>The items the model drafts most often could not fill from the chart, so a clinician had to confirm or write them. Long bars point to documentation that is often missing in the ED.</p>,
      },
      {
        id: 'quality',
        title: 'Completeness and severity at transfer',
        body: <p><b>Completeness at transfer</b>: how many handoffs reached each level before clinician edits. <b>Illness severity at transfer</b>: how many patients were stable, watcher or unstable when they left the ED.</p>,
      },
    ],
  },
  {
    id: 'templates',
    title: 'Templates',
    summary: 'What each handoff contains, and which items must be filled.',
    kind: 'reference',
    topics: [
      {
        id: 'read',
        title: 'How to read a template',
        body: (
          <>
            <p>Pick a tab: Nurse handoff (SBAR), Physician handoff (I-PASS), or Custom handoff (Joint Commission minimum). Each item shows its name, a short code, what it holds and two tags:</p>
            <Terms
              items={[
                ['From the chart', 'Copied from the record. It cannot be edited here.'],
                ['Model drafted', 'Written by the model. You review it and can edit it.'],
                ['In the app', 'Filled when you sign or accept: sender, callback, read back.'],
                [<Pill key="p" tone="danger">Blocks sign off</Pill>, 'Must be documented or confirmed before signing.'],
                [<Pill key="p" tone="warn">Must be recent</Pill>, 'Must be recent enough for the severity; the minutes are listed.'],
                [<Pill key="p">Warning</Pill>, 'Counts toward completeness, never blocks.'],
                [<Pill key="p" tone="info">Checked at sign</Pill>, 'Completed by the sign off or the acceptance itself.'],
              ]}
            />
          </>
        ),
      },
      {
        id: 'change',
        title: 'Changing a template',
        body: <p>Your informatics team can rename sections, move items and change which items block signing. They edit the template files in the app's repository, and the next drafts follow the new template.</p>,
      },
    ],
  },
  {
    id: 'clock',
    title: 'The clock',
    summary: 'The date and time at the top right decide which moment you are looking at.',
    kind: 'reference',
    topics: [
      {
        id: 'what',
        title: 'What the clock shows',
        body: (
          <>
            <p>
              This demo replays one past week of made up patients. The date and time <Key><CalendarClock />Wed, Oct 28 16:00</Key> is the moment you are looking at. Every page shows the hospital as it was then: who is in the ED, who is waiting for a bed, the latest draft of each handoff, and what has been signed. Times are the hospital's local time.
            </p>
            <p className="text-muted-foreground">The first time you open the demo, a short note under the clock says so. Close it with its x; it does not come back.</p>
            <Note>In a hospital the clock reads Live and always shows the current time. There is nothing to set.</Note>
          </>
        ),
      },
      {
        id: 'controls',
        title: 'The buttons',
        body: (
          <Terms
            items={[
              [<span key="k" className="inline-flex gap-1"><Key label="back"><ChevronLeft /></Key><Key label="forward"><ChevronRight /></Key></span>, 'Back or forward one hour.'],
              [<Key key="k"><Play />Play</Key>, 'Moves the clock forward 15 minutes every 1.5 seconds, so you can watch patients arrive, get beds and go up. Press again to pause.'],
              [<Key key="k"><CalendarClock />Date</Key>, <>Opens a panel: drag the slider to any time in the week, pick a day (midnight to midnight at the hospital; the time of day stays the same, even across a clock change), go back to the <b>Busiest hour</b>, or <Key><RotateCcw />Clear all demo actions</Key>, which asks first.</>],
            ]}
          />
        ),
      },
      {
        id: 'actions',
        title: 'Your actions and the clock',
        body: (
          <p>
            What you do (edit, attest, sign, accept) is recorded at the time on the clock. Move the clock back before that time and it has not happened yet; move forward and it is there again. You cannot act at a time earlier than something already recorded on that handoff: move the clock to now first. Once a patient leaves the ED, their handoff is final, unless it was never signed. The time is part of the page address, so a reload or a shared link opens at the same moment. To start the demo over, open the date panel and press Clear all demo actions.
          </p>
        ),
      },
      {
        id: 'demo',
        title: 'Synthetic data',
        body: __STATIC_DEMO__ ? (
          <>
            <p>The <Key>Synthetic data</Key> label is a reminder: every patient, name and number in this demo is invented. Nothing comes from a real hospital.</p>
            <Note>In this public web demo everything runs in your browser. What you sign or accept is kept only in this tab and is cleared when you reload the page.</Note>
          </>
        ) : (
          <p>The <Key>Synthetic data</Key> label is a reminder: every patient, name and number in this demo is invented. Nothing comes from a real hospital.</p>
        ),
      },
    ],
  },
  {
    id: 'sidebar',
    title: 'The sidebar and role menu',
    nav: 'Sidebar and role menu',
    summary: 'Pages, who you are, and the display buttons.',
    kind: 'reference',
    topics: [
      {
        id: 'pages',
        title: 'Pages',
        body: (
          <Terms
            items={[
              ['Board', 'Your work list: every patient with a handoff right now. Start here.'],
              ['Analytics', 'How the week has gone: how busy the ED was, how long patients waited, what handoffs were missing.'],
              ['Templates', 'What each kind of handoff contains, and which items must be filled before signing.'],
              ['How to use', 'This guide.'],
            ]}
          />
        ),
      },
      {
        id: 'you',
        title: 'The role menu',
        body: (
          <>
            <p>
              The bottom of the sidebar shows who you are, for example <Key>Jordan Reyes, RN · ED nurse<ChevronsUpDown /></Key>. In the demo, click it to become someone else and see exactly what that person sees and can do. The people are grouped: <b>Emergency department</b>, <b>Admitting team</b> and <b>Receiving units</b>, one nurse per unit.
            </p>
            <p className="text-muted-foreground">In a hospital you are always yourself, and your role comes from your account. Someone signed in without a role can read everything but change nothing.</p>
          </>
        ),
      },
      {
        id: 'buttons',
        title: 'Theme and sidebar buttons',
        body: (
          <Terms
            items={[
              [<span key="k" className="inline-flex gap-1"><Key label="moon"><Moon /></Key><Key label="sun"><Sun /></Key></span>, 'Switches between the light and the dark theme.'],
              [<Key key="k" label="collapse sidebar"><PanelLeftClose /></Key>, 'Folds the sidebar to icons for more room. Press again to unfold.'],
              [<Key key="k" label="menu"><Menu /></Key>, 'On a phone or a small tablet the sidebar is hidden. This button at the top left opens it.'],
            ]}
          />
        ),
      },
    ],
  },

  // ---------------------------------------------------------------------------------- glossary
  {
    id: 'glossary',
    title: 'Glossary',
    summary: 'Words used in the app, in plain language.',
    kind: 'glossary',
    topics: [
      { id: 'addendum', title: 'Addendum', body: 'A new version written after the patient left the ED, for an ED note signed late or a critical result from a test done in the ED. The ED clinician can still sign it.' },
      { id: 'admit-decision', title: 'Admit decision', body: 'The moment the ED decides the patient needs a bed. The first draft is written then.' },
      { id: 'attest', title: 'Attest', body: 'Choose from a fixed list what you checked, such as "No known allergies confirmed", or enter what you found, such as Droplet isolation. It records that you checked.' },
      { id: 'boarding', title: 'Boarding', body: 'Waiting in the ED after the admit decision, until the patient leaves for the unit.' },
      { id: 'callback', title: 'Callback', body: 'The phone or extension number the receiver can call the sender on: at least 3 digits, or an extension like x4471. Required to sign.' },
      { id: 'changed', title: 'Changed after signing', body: 'An edit, an attestation or a newer draft with different content came after the signature. The ED signs again before the receiver can accept.' },
      { id: 'completeness', title: 'Completeness', body: "The share of the template's checks that are met or resolved. Required items block signing; warnings only lower the number." },
      { id: 'contingency', title: 'Contingency plan', body: 'If this happens, do that: how the patient could get worse, and what to do about it.' },
      { id: 'entered', title: 'Entered, not yet in the chart', body: 'A value the ED clinician entered in the sign off (isolation, code status, an allergy) because the chart had none. The chart value replaces it once a newer draft has one.' },
      { id: 'esi', title: 'ESI', body: 'Emergency Severity Index: the triage level, from 1 (most urgent) to 5.' },
      { id: 'ipass', title: 'I-PASS', body: 'Illness severity, Patient summary, Action list, Situation awareness and contingency, Synthesis by the receiver. The physician handoff format.' },
      { id: 'minimum', title: 'Joint Commission minimum', body: 'A short handoff with the items the Joint Commission says every handoff needs. Any clinician can use it.' },
      { id: 'level-of-care', title: 'Level of care', body: 'ICU, step down, telemetry, med surg or observation.' },
      { id: 'model', title: 'Model', body: 'The AI language model that writes the narrative parts of each draft from the chart. A person always checks and signs.' },
      { id: 'mrn', title: 'MRN', body: 'Medical record number.' },
      { id: 'read-back', title: 'Read back', body: 'The receiver repeats the key points in their own words, to show they understood: at least a sentence. Required to accept.' },
      { id: 'rule-floor', title: 'Rule floor', body: 'The lowest severity the chart allows, set by fixed rules. The model can raise severity above it, never lower.' },
      { id: 'sbar', title: 'SBAR', body: 'Situation, Background, Assessment, Recommendation. The nurse handoff format.' },
      { id: 'severity', title: 'Severity', body: 'How sick the patient is: stable (expected to stay well), watcher (could get worse, keep a close eye) or unstable (needs action now).' },
      { id: 'source', title: 'Source', body: 'The chart record a statement came from: a note, a lab, a medication, a set of vital signs.' },
      { id: 'stale', title: 'Stale', body: 'Too old. Vital signs go stale after a limit that depends on the severity and the template: SBAR 60, 30 or 15 minutes for a stable, watcher or unstable patient; I-PASS 120, 60 or 30. A bedside recheck counts for the same limit.' },
      { id: 'unsigned-transfer', title: 'Transferred without sign off', body: 'The patient left the ED before the handoff was signed. The ED can still complete and sign it; the board counts these under To sign as "left without sign off".' },
      { id: 'version', title: 'Version', body: 'Each time the chart changes in a way that matters, a new draft is written. The newest is shown, and History lists them all.' },
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

/** The guide page anchor for a topic id ("handoff.sign" is #handoff-sign, "board" is #board). */
export function anchorFor(id: string): string {
  return id.replace('.', '-');
}
