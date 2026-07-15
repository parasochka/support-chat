import { Title } from 'react-admin';
import Box from '@mui/material/Box';
import Card from '@mui/material/Card';
import CardContent from '@mui/material/CardContent';
import Divider from '@mui/material/Divider';
import Table from '@mui/material/Table';
import TableBody from '@mui/material/TableBody';
import TableCell from '@mui/material/TableCell';
import TableHead from '@mui/material/TableHead';
import TableRow from '@mui/material/TableRow';
import Typography from '@mui/material/Typography';
import RequireProduct from '../components/RequireProduct';
import { t } from '../i18n';
import rich from '../components/Rich';

/**
 * The operator's guide to the whole support-chat module — the support twin of
 * the Proactive agent's "How it works & testing" tab. Static content (no API
 * calls), so it renders at any scope; every string routes through t() and the
 * inline-markup helper so the page is fully bilingual.
 */

const Section = ({ title, children }) => (
  <Box sx={{ mb: 3 }}>
    <Typography variant="h6" sx={{ mb: 1 }}>
      {title}
    </Typography>
    {children}
  </Box>
);

const P = ({ children }) => (
  <Typography variant="body2" sx={{ mb: 1 }}>
    {children}
  </Typography>
);

const LI = ({ children }) => (
  <Typography component="li" variant="body2" sx={{ mb: 0.5 }}>
    {children}
  </Typography>
);

// Where each kind of content is edited: the "one home per thing" map.
const CONTENT_MAP = [
  [
    'Answers to player questions',
    'Knowledge base',
    'One KB text per topic. The assistant answers STRICTLY from it — facts missing here are the #1 reason for vague answers or escalations.',
  ],
  [
    'Numbers, amounts, timeframes',
    'Knowledge base → Variables',
    'Reusable `{placeholder}` values substituted into every KB text — change a limit once, it updates everywhere.',
  ],
  [
    'Persona: name, brand, tone of voice',
    'Prompt → Prompt variables',
    'The values that uniquify the shared prompt template for this brand. The wording around them is fixed in code.',
  ],
  [
    'Escalation trigger words',
    'Common → Escalation keywords',
    'Two keyword lists (high-risk + "call a human") checked BEFORE the model — a match hands off without burning tokens.',
  ],
  [
    'Everything the player reads in the widget',
    'Translations',
    'Widget chrome, service replies, the escalation card and its per-language contact link (`contact_url`), topic names — per language.',
  ],
  [
    'Pages the assistant may link to',
    'Site map',
    'The official site pages (shared with the Telegram bot). The assistant never invents URLs — it links only these.',
  ],
  [
    'Anti-spam and chat limits',
    'Chat settings',
    'Rate limits, cooldowns, message caps, the injection and low-content guards.',
  ],
  [
    'Model, languages, technical limits',
    'System → Settings',
    'Shared by both bots: the OpenAI model and its budgets, the supported languages, request limits.',
  ],
];

const SupportGuideInner = () => (
  <Box sx={{ p: 2 }}>
    <Title title={t('Support chat — how it works')} />
    <Typography variant="body2" color="text.secondary" sx={{ mb: 2 }}>
      {t(
        'The operator’s guide to the on-site support chat: what happens to a player’s message, where every piece of content is edited, and how to test the whole flow before going live.'
      )}
    </Typography>

    <Card>
      <CardContent>
        <Section title={t('What the support chat is')}>
          <P>
            {rich(
              t(
                'A chat widget on the casino site where the AI persona answers player questions strictly from this product’s **Knowledge base**. The player picks a topic, chats in their own language, and either gets the answer or is handed off to a human via the escalation card. The widget is embedded with one script tag — the snippet (with this product’s widget key) is in **Structure**.'
              )
            )}
          </P>
        </Section>
        <Divider sx={{ mb: 2 }} />

        <Section title={t('The path of one message')}>
          <P>{t('Every player message goes through the same pipeline, in order:')}</P>
          <Box component="ol" sx={{ pl: 3, my: 0 }}>
            <LI>
              {rich(
                t(
                  '**Anti-spam gates** — rate limit per IP, a cooldown between messages, a length cap, the low-content guard (one-character spam gets a nudge without a model call) and the prompt-injection scan. All tunable in **Chat settings**; a rejected message never reaches the model, so attacks don’t burn tokens.'
                )
              )}
            </LI>
            <LI>
              {rich(
                t(
                  '**Keyword escalation check** — if the message hits a high-risk stem (fraud, legal threats) or an explicit "call a human", the escalation card is shown immediately, before any model call. The lists are edited in **Common → Escalation keywords**.'
                )
              )}
            </LI>
            <LI>
              {rich(
                t(
                  '**Prompt assembly** — three layers: the fixed persona + rules (rendered with your prompt variables), the selected topic’s KB text, and the per-message data (player profile, conversation history, language). Only the selected topic’s KB is loaded — that’s why topic routing matters.'
                )
              )}
            </LI>
            <LI>
              {rich(
                t(
                  '**Model answer** — the AI answers in the player’s language and may attach service signals: a topic switch, follow-up suggestions, a "question resolved" flag, or an escalation. The signals are stripped from the text and become widget behaviour.'
                )
              )}
            </LI>
            <LI>
              {rich(
                t(
                  '**Persistence** — the turn, its token cost and every state change are stored; you see them in **Conversations** (full transcripts with per-turn cost) and on the **Dashboard**.'
                )
              )}
            </LI>
          </Box>
        </Section>
        <Divider sx={{ mb: 2 }} />

        <Section title={t('Where every piece of content is edited')}>
          <P>
            {t(
              'One home per thing — if a text or number is wrong in the chat, this table says where to fix it:'
            )}
          </P>
          <Box sx={{ overflowX: 'auto' }}>
            <Table size="small" sx={{ minWidth: 640 }}>
              <TableHead>
                <TableRow>
                  <TableCell>{t('What')}</TableCell>
                  <TableCell>{t('Where to edit')}</TableCell>
                  <TableCell>{t('Notes')}</TableCell>
                </TableRow>
              </TableHead>
              <TableBody>
                {CONTENT_MAP.map(([what, where, notes]) => (
                  <TableRow key={what}>
                    <TableCell>{t(what)}</TableCell>
                    <TableCell sx={{ whiteSpace: 'nowrap' }}>
                      <b>{t(where)}</b>
                    </TableCell>
                    <TableCell>{rich(t(notes))}</TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </Box>
          <P>
            {rich(
              t(
                'The prompt WORDING itself (the rules around your variables) is deliberately not editable here — it lives in the code as the one shared template, so every brand runs the same tested behaviour. What you can always do is READ it: **Prompt → Preview** shows the complete assembled prompt exactly as the model receives it.'
              )
            )}
          </P>
        </Section>
        <Divider sx={{ mb: 2 }} />

        <Section title={t('Topics and automatic routing')}>
          <Box component="ul" sx={{ pl: 3, my: 0 }}>
            <LI>
              {rich(
                t(
                  'The player picks a topic first; only that topic’s KB is loaded. The topic buttons and their per-language names come from **Knowledge base** + **Translations → Topic names**.'
                )
              )}
            </LI>
            <LI>
              {rich(
                t(
                  '**Wrong-topic questions route automatically**: when a question plainly belongs to another topic, the widget shows a "switching to …" notice, switches, and re-asks the question against the right KB — the player never sees an answer produced without the matching KB.'
                )
              )}
            </LI>
            <LI>
              {rich(
                t(
                  '`other` is the general entry topic with its own KB. It routes players onward more often, but answers from its own KB exactly like the rest.'
                )
              )}
            </LI>
          </Box>
        </Section>
        <Divider sx={{ mb: 2 }} />

        <Section title={t('Escalation — how a chat reaches a human')}>
          <Box component="ul" sx={{ pl: 3, my: 0 }}>
            <LI>
              {rich(
                t(
                  '**Soft** (trigger words): the contact card is shown but the chat stays open — a false positive never kills a live conversation.'
                )
              )}
            </LI>
            <LI>
              {rich(
                t(
                  '**Hard** (the model gives up, the message cap, or the player taps the escalate button): the card is shown and the conversation ends.'
                )
              )}
            </LI>
            <LI>
              {rich(
                t(
                  'The card’s button target is the per-language `contact_url` in **Translations**. When this product’s **Telegram retention bot** is enabled, the button instead deep-links the player straight into the bot (subscription gate on the way in, "go to a manager" in its menu).'
                )
              )}
            </LI>
            <LI>
              {rich(
                t(
                  'Escalated and abandoned chats queue up in **Escalations** for triage, grouped by topic.'
                )
              )}
            </LI>
          </Box>
        </Section>
        <Divider sx={{ mb: 2 }} />

        <Section title={t('Suggestions and finishing a chat')}>
          <Box component="ul" sx={{ pl: 3, my: 0 }}>
            <LI>
              {t(
                'After an answer the assistant may offer up to two one-tap follow-up questions whose answers ARE in the KB — they pull the player toward the exact entry they need.'
              )}
            </LI>
            <LI>
              {t(
                'A separate green option lets the player close the chat ("Issue solved."); when the assistant judges the question fully answered, the widget also shows a "finish chat" button. A finished chat is marked resolved and leaves the open-sessions metric.'
              )}
            </LI>
          </Box>
        </Section>
        <Divider sx={{ mb: 2 }} />

        <Section title={t('Languages')}>
          <Box component="ul" sx={{ pl: 3, my: 0 }}>
            <LI>
              {t(
                'The widget opens in the browser’s language; the ANSWERS follow the player — switch language mid-chat and the assistant (and the widget chrome) switch too.'
              )}
            </LI>
            <LI>
              {rich(
                t(
                  'The supported set and the default live in **System → Settings → Languages**. A newly added language starts on English copy and becomes fully translatable in **Translations**.'
                )
              )}
            </LI>
            <LI>
              {rich(
                t(
                  'The KB stays in English on purpose (most token-efficient for the model) — the assistant still answers in the player’s language.'
                )
              )}
            </LI>
          </Box>
        </Section>
        <Divider sx={{ mb: 2 }} />

        <Section title={t('How to test, step by step')}>
          <Box component="ol" sx={{ pl: 3, my: 0 }}>
            <LI>
              {rich(
                t(
                  'Select the product in the header switcher and check its content: topics + KB texts (**Knowledge base**), persona values (**Prompt → Prompt variables**), the contact link and widget copy (**Translations**).'
                )
              )}
            </LI>
            <LI>
              {rich(
                t(
                  'Set the **Test player** profile (Common → Test player profile) — on a test deploy without the site handshake it stands in for the real player, so you can check the by-name greeting and VIP personalization.'
                )
              )}
            </LI>
            <LI>
              {rich(
                t(
                  'Open the test page (the service root `/`) or embed the snippet from **Structure** on a staging page, pick a topic and ask real questions from the KB — including ones phrased differently from how the KB is written.'
                )
              )}
            </LI>
            <LI>
              {rich(
                t(
                  'Ask a question that belongs to ANOTHER topic and watch the automatic switch notice + the re-ask. Then trigger an escalation ("I want to talk to a human") and check the card — its button, language, and (with retention on) the bot deeplink.'
                )
              )}
            </LI>
            <LI>
              {rich(
                t(
                  'Review the results in **Conversations** (transcript, per-turn cost, switch markers) and the **Dashboard** (sessions, escalation rate, cost). Wrong or vague answers almost always mean a KB gap — fix the KB text, not the prompt.'
                )
              )}
            </LI>
          </Box>
        </Section>
        <Divider sx={{ mb: 2 }} />

        <Section title={t('Costs')}>
          <P>
            {rich(
              t(
                'Each answer is one model call; its token cost is stored per turn and summed per session, topic and language on the **Dashboard**. The prompt is built so its expensive fixed part is cached by the provider — editing prompt variables or a KB text resets that cache briefly, which is normal.'
              )
            )}
          </P>
        </Section>
      </CardContent>
    </Card>
  </Box>
);

// The guide describes per-product content (KB, prompt variables, translations)
// — like the rest of the Support chat section it renders only in a concrete
// product context.
const SupportGuide = () => (
  <RequireProduct title={t('Support chat — how it works')}>
    <SupportGuideInner />
  </RequireProduct>
);

export default SupportGuide;
