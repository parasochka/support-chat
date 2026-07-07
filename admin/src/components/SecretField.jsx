import Box from '@mui/material/Box';
import Button from '@mui/material/Button';
import InputAdornment from '@mui/material/InputAdornment';
import Stack from '@mui/material/Stack';
import TextField from '@mui/material/TextField';
import SetBadge from './SetBadge';

/**
 * A write-only secret input with an explicit "configured?" state.
 *
 *  - a green check (SetBadge) shows when the value is currently set, a muted
 *    circle when it is not — replacing the ambiguous "· set / · not set" text;
 *  - a Clear button lets the operator SAVE AN EMPTY VALUE (clear the stored
 *    secret / fall back to env), which typing nothing could not express before.
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
  helperText,
  size = 'small',
}) => (
  <Box>
    <TextField
      label={label}
      type="password"
      value={value ?? ''}
      onChange={onChange}
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
                {onClear && set && (
                  <Button size="small" color="warning" onClick={onClear}>
                    Clear
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

export default SecretField;
