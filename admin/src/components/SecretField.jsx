import { useState } from 'react';
import Box from '@mui/material/Box';
import Button from '@mui/material/Button';
import InputAdornment from '@mui/material/InputAdornment';
import Stack from '@mui/material/Stack';
import TextField from '@mui/material/TextField';
import { generateSecret } from '../lib/secrets';
import SetBadge from './SetBadge';
import { t } from '../i18n';

/**
 * A write-only secret input with an explicit "configured?" state.
 *
 *  - a green check (SetBadge) shows when the value is currently set, a muted
 *    circle when it is not — replacing the ambiguous "· set / · not set" text;
 *  - a Clear button lets the operator SAVE AN EMPTY VALUE (clear the stored
 *    secret / fall back to env), which typing nothing could not express before;
 *  - with `onGenerate`, a Generate button fills the field with a fresh random
 *    signing key (browser CSPRNG) and reveals it so it can be copied to the
 *    host-site config — for secrets WE mint (handshake secret), not for
 *    externally-issued keys (OpenAI, Telegram).
 *
 * The field never shows the stored secret back; typing sets a new value, Clear
 * asks the parent to persist an empty one.
 */
const SecretField = ({
  label,
  set,
  value,
  onChange,
  onClear,
  onGenerate,
  helperText,
  size = 'small',
}) => {
  // Generated values render as visible text (the operator must copy them);
  // hand-typed values stay masked.
  const [revealed, setRevealed] = useState(false);

  const generate = () => {
    setRevealed(true);
    onGenerate(generateSecret());
  };

  return (
    <Box>
      <TextField
        label={label}
        type={revealed && value ? 'text' : 'password'}
        value={value ?? ''}
        onChange={(e) => {
          setRevealed(false);
          onChange(e);
        }}
        fullWidth
        size={size}
        margin="dense"
        autoComplete="new-password"
        helperText={helperText}
        slotProps={{
          input: {
            endAdornment: (
              <InputAdornment position="end">
                <Stack direction="row" spacing={0.5} alignItems="center">
                  <SetBadge set={set} />
                  {onGenerate && (
                    <Button size="small" onClick={generate} sx={{ whiteSpace: 'nowrap' }}>
                      {t('Generate')}
                    </Button>
                  )}
                  {onClear && set && (
                    <Button size="small" color="warning" onClick={onClear}>
                      {t('Clear')}
                    </Button>
                  )}
                </Stack>
              </InputAdornment>
            ),
          },
        }}
      />
    </Box>
  );
};

export default SecretField;
