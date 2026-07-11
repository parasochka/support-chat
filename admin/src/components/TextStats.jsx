import Stack from '@mui/material/Stack';
import Tooltip from '@mui/material/Tooltip';
import Typography from '@mui/material/Typography';
import { useModelPricing } from '../lib/meta';
import { t } from '../i18n';

/**
 * Live character / ≈token / cost line for prompt and KB editors, so an
 * operator sees the volume of what they are writing while they write it.
 *
 * Token count is a client-side ESTIMATE (no tokenizer in the bundle): Latin
 * text averages ~4 chars/token on OpenAI tokenizers, Cyrillic ~2, digits and
 * punctuation are denser. Close enough to steer editing; the exact number is
 * whatever the tokenizer says at request time.
 *
 * Cost = estimated tokens × the CURRENT model's UNCACHED input price
 * (GET /admin/meta → model_pricing). One prompt of this text costs this much
 * on a cold cache; the prefix cache makes repeat sends cheaper.
 */
export const estimateTokens = (text) => {
  if (!text) return 0;
  const count = (re) => (text.match(re) || []).length;
  const latin = count(/[A-Za-z]/g);
  const cyr = count(/[Ѐ-ӿ]/g);
  const digit = count(/[0-9]/g);
  const space = count(/\s/g);
  const other = Math.max(0, text.length - latin - cyr - digit - space);
  // Whitespace mostly merges into neighbouring tokens; count a fraction.
  return Math.max(
    text.trim() ? 1 : 0,
    Math.round(latin / 4 + cyr / 2 + digit / 3 + other / 2 + space / 8)
  );
};

const fmtInt = (n) => n.toLocaleString('en-US');

const fmtUsd = (usd) => {
  if (usd >= 0.01) return `$${usd.toFixed(3)}`;
  if (usd >= 0.0001) return `$${usd.toFixed(5)}`;
  return usd > 0 ? `$${usd.toExponential(1)}` : '$0';
};

/**
 * Props: `text` — the live text (string or array of strings to sum);
 * `label` — optional prefix (e.g. "Total").
 */
const TextStats = ({ text, label, sx }) => {
  const mp = useModelPricing();
  const joined = Array.isArray(text) ? text.filter(Boolean).join('\n') : text || '';
  const chars = joined.length;
  const tokens = estimateTokens(joined);
  const price = mp?.pricing?.input_per_1m;
  const cost = price != null ? (tokens * price) / 1_000_000 : null;

  return (
    <Stack
      direction="row"
      spacing={1.5}
      flexWrap="wrap"
      useFlexGap
      alignItems="baseline"
      sx={{ my: 0.5, ...sx }}
    >
      {label && (
        <Typography variant="caption" sx={{ fontWeight: 600 }}>
          {label}
        </Typography>
      )}
      <Typography variant="caption" color="text.secondary">
        {fmtInt(chars)} {t('characters')}
      </Typography>
      <Typography variant="caption" color="text.secondary">
        ≈{fmtInt(tokens)} {t('tokens (approx.)')}
      </Typography>
      {cost != null && (
        <Tooltip
          title={`${mp.model}: $${price}/1M input tokens (${t('uncached input')})`}
        >
          <Typography variant="caption" color="text.secondary" sx={{ cursor: 'help' }}>
            ≈{fmtUsd(cost)} {t('per prompt')} · {mp.model}
          </Typography>
        </Tooltip>
      )}
    </Stack>
  );
};

export default TextStats;
