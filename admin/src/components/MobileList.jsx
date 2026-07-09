import { useListContext } from 'react-admin';
import List from '@mui/material/List';
import ListItemButton from '@mui/material/ListItemButton';
import ListItemText from '@mui/material/ListItemText';
import Typography from '@mui/material/Typography';
import CircularProgress from '@mui/material/CircularProgress';
import Box from '@mui/material/Box';

/**
 * Compact card-style rows for phones, replacing a wide <Datagrid> that would
 * force full-page horizontal scrolling. Reads rows from the ListContext so it
 * drops straight into a react-admin <List>.
 *
 * primaryText / secondaryText / tertiaryText mirror SimpleList's contract;
 * onRowClick(id, record) makes the whole row a tap target.
 */
const MobileList = ({ primaryText, secondaryText, tertiaryText, onRowClick }) => {
  const { data, isPending } = useListContext();
  if (isPending) {
    return (
      <Box sx={{ display: 'flex', justifyContent: 'center', py: 4 }}>
        <CircularProgress size={28} />
      </Box>
    );
  }
  if (!data || data.length === 0) {
    return (
      <Typography color="text.secondary" sx={{ p: 2 }}>
        No results.
      </Typography>
    );
  }
  return (
    <List disablePadding>
      {data.map((record) => (
        <ListItemButton
          key={record.id}
          divider
          onClick={onRowClick ? () => onRowClick(record.id, record) : undefined}
          sx={{ alignItems: 'flex-start', flexDirection: 'column', gap: 0.25, py: 1.25 }}
        >
          <ListItemText
            sx={{ m: 0, width: '100%' }}
            primary={primaryText(record)}
            secondary={
              <>
                {secondaryText && (
                  <Typography
                    component="span"
                    variant="body2"
                    color="text.secondary"
                    sx={{
                      display: 'block',
                      overflow: 'hidden',
                      textOverflow: 'ellipsis',
                      whiteSpace: 'nowrap',
                    }}
                  >
                    {secondaryText(record)}
                  </Typography>
                )}
                {tertiaryText && (
                  <Typography component="span" variant="caption" color="text.secondary">
                    {tertiaryText(record)}
                  </Typography>
                )}
              </>
            }
          />
        </ListItemButton>
      ))}
    </List>
  );
};

export default MobileList;
