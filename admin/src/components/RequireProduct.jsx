import Box from '@mui/material/Box';
import Alert from '@mui/material/Alert';
import AlertTitle from '@mui/material/AlertTitle';
import { Title } from 'react-admin';
import { getProductId } from '../productScope';

/**
 * Gate for surfaces whose data belongs to a single product (KB, Prompt,
 * Translations, Retention). Editing them while the header shows "All products"
 * silently resolves to the default product — confusing, because it looks like
 * you are editing global data. So unless a concrete product is selected we
 * refuse to render and ask the operator to pick one in the header switcher.
 *
 * Applies to admins and managers alike (managers get read-only controls inside,
 * but still need a product context for the data to mean anything).
 */
const RequireProduct = ({ title, children }) => {
  if (getProductId()) return children;
  return (
    <Box sx={{ p: 2 }}>
      {title && <Title title={title} />}
      <Alert severity="info" sx={{ maxWidth: 640 }}>
        <AlertTitle>Please select a product</AlertTitle>
        This screen shows data for a single product. Pick a product in the
        Partner → Product switcher at the top-right to continue.
      </Alert>
    </Box>
  );
};

export default RequireProduct;
