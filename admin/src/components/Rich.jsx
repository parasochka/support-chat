/**
 * Renders a translated plain string with a tiny inline markup subset:
 * `code` → <code>, **bold** → <b>, [label](url) → <a>. This keeps long
 * guide/help copy translatable as ONE i18n dictionary string instead of
 * fragmented JSX — the translator sees the whole sentence.
 *
 * Usage: <P>{rich(t('Press **Save** and call `POST /x` — see [docs](#/kb).'))}</P>
 */
import { Fragment } from 'react';
import Link from '@mui/material/Link';

const TOKEN_RE = /(`[^`]+`|\*\*[^*]+\*\*|\[[^\]]+\]\([^)\s]+\))/g;
const LINK_RE = /^\[([^\]]+)\]\(([^)\s]+)\)$/;

export const rich = (text) =>
  String(text ?? '')
    .split(TOKEN_RE)
    .map((part, i) => {
      if (part.startsWith('`') && part.endsWith('`')) {
        return <code key={i}>{part.slice(1, -1)}</code>;
      }
      if (part.startsWith('**') && part.endsWith('**')) {
        return <b key={i}>{part.slice(2, -2)}</b>;
      }
      const m = part.match(LINK_RE);
      if (m) {
        const external = /^https?:\/\//.test(m[2]);
        return (
          <Link
            key={i}
            href={m[2]}
            {...(external ? { target: '_blank', rel: 'noopener' } : {})}
          >
            {m[1]}
          </Link>
        );
      }
      return <Fragment key={i}>{part}</Fragment>;
    });

export default rich;
