import Box from '@mui/material/Box';
import Tooltip from '@mui/material/Tooltip';
import CheckCircleIcon from '@mui/icons-material/CheckCircle';
import RadioButtonUncheckedIcon from '@mui/icons-material/RadioButtonUnchecked';

/**
 * Explicit "is this value configured?" indicator. Replaces the old ambiguous
 * "· set" / "· not set" text: a green check when a value is present, a muted
 * hollow circle when it is not, so the state reads at a glance and looks the
 * same everywhere it appears (secrets, Telegram config, …).
 */
const SetBadge = ({ set, setLabel = 'Set', unsetLabel = 'Not set', sx }) => (
  <Tooltip title={set ? setLabel : unsetLabel}>
    <Box
      component="span"
      sx={{ display: 'inline-flex', alignItems: 'center', verticalAlign: 'middle', ...sx }}
    >
      {set ? (
        <CheckCircleIcon fontSize="small" sx={{ color: 'success.main' }} />
      ) : (
        <RadioButtonUncheckedIcon fontSize="small" sx={{ color: 'text.disabled' }} />
      )}
    </Box>
  </Tooltip>
);

export default SetBadge;
