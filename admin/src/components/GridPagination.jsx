import Stack from '@mui/material/Stack';
import Typography from '@mui/material/Typography';
import MuiPagination from '@mui/material/Pagination';
import { t } from '../i18n';

// A pagination bar that mirrors react-admin's default <Pagination> look (the
// Conversations list): a "1–N of M" range on the left and numbered page buttons
// on the right. Used by the client-paginated grids/tables in this page so they
// match the rest of the admin. `count` is the total row count, `page` is
// 1-based, `perPage` the page size.
const GridPagination = ({ count, page, perPage, onPage, unit = t('items') }) => {
  const pageCount = Math.max(1, Math.ceil(count / perPage));
  const from = count === 0 ? 0 : (page - 1) * perPage + 1;
  const to = Math.min(page * perPage, count);
  return (
    <Stack
      direction="row"
      alignItems="center"
      justifyContent="space-between"
      flexWrap="wrap"
      useFlexGap
      spacing={1}
      sx={{ mt: 2, px: 1 }}
    >
      <Typography variant="body2" color="text.secondary">
        {from}–{to} {t('of')} {count} {unit}
      </Typography>
      <MuiPagination
        color="primary"
        size="small"
        count={pageCount}
        page={Math.min(page, pageCount)}
        onChange={(_e, value) => onPage(value)}
        showFirstButton
        showLastButton
      />
    </Stack>
  );
};

export default GridPagination;
