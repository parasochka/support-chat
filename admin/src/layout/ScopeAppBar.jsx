import { useEffect, useState } from 'react';
import { AppBar, TitlePortal } from 'react-admin';
import ListSubheader from '@mui/material/ListSubheader';
import MenuItem from '@mui/material/MenuItem';
import Select from '@mui/material/Select';
import { API_URL, httpClient, getToken } from '../httpClient';
import { getPartnerId, getProductId, setScope } from '../productScope';

/**
 * AppBar with the Partner → Product switcher (GET /admin/structure). Changing
 * the selection persists it and reloads so every view re-fetches in scope.
 */
const ScopeSelect = () => {
  const [structure, setStructure] = useState(null);

  useEffect(() => {
    if (!getToken()) return;
    httpClient(`${API_URL}/admin/structure`)
      .then(({ json }) => {
        // A persisted scope pointing at a product/partner that no longer
        // exists (deleted, or this account lost access) would wedge every
        // RequireProduct page on 403/404 toasts — reset to All and reload.
        const pid = getProductId();
        const paid = getPartnerId();
        const partners = json.partners || [];
        const productOk =
          !pid || partners.some((pa) => (pa.products || []).some((pr) => pr.id === pid));
        const partnerOk = !paid || partners.some((pa) => pa.id === paid);
        if (!productOk || !partnerOk) {
          setScope({});
          window.location.reload();
          return;
        }
        setStructure(json);
      })
      .catch(() => setStructure(null));
  }, []);

  if (!structure) return null;

  const productId = getProductId();
  const partnerId = getPartnerId();
  const value = productId
    ? `product:${productId}`
    : partnerId
      ? `partner:${partnerId}`
      : 'all';

  const onChange = (e) => {
    const v = e.target.value;
    if (v === 'all') {
      setScope({});
    } else if (v.startsWith('partner:')) {
      const id = Number(v.slice(8));
      const pa = (structure.partners || []).find((x) => x.id === id);
      setScope({ partnerId: id, name: pa ? `${pa.name} — all products` : '' });
    } else {
      const id = Number(v.slice(8));
      let name = '';
      (structure.partners || []).forEach((pa) =>
        (pa.products || []).forEach((pr) => {
          if (pr.id === id) name = pr.name;
        })
      );
      setScope({ productId: id, name });
    }
    window.location.reload();
  };

  const items = [
    <MenuItem key="all" value="all">
      All products
    </MenuItem>,
  ];
  (structure.partners || []).forEach((pa) => {
    items.push(
      <ListSubheader key={`h${pa.id}`}>{pa.name}</ListSubheader>,
      <MenuItem key={`pa${pa.id}`} value={`partner:${pa.id}`}>
        {pa.name} — all
      </MenuItem>
    );
    (pa.products || []).forEach((pr) => {
      items.push(
        <MenuItem key={`pr${pr.id}`} value={`product:${pr.id}`} sx={{ pl: 4 }}>
          {pr.name}
        </MenuItem>
      );
    });
  });

  return (
    <Select
      size="small"
      value={value}
      onChange={onChange}
      variant="outlined"
      sx={{
        // keep the switcher from shrinking away and from touching its neighbours
        flexShrink: 0,
        mx: { xs: 0.75, sm: 1.5 },
        minWidth: { xs: 120, sm: 180 },
        maxWidth: { xs: 160, sm: 300 },
        '.MuiSelect-select': {
          py: 0.5,
          whiteSpace: 'nowrap',
          overflow: 'hidden',
          textOverflow: 'ellipsis',
        },
      }}
    >
      {items}
    </Select>
  );
};

const ScopeAppBar = () => (
  <AppBar>
    {/* The title is the flex-grower: it takes the slack and truncates with an
        ellipsis (minWidth: 0) so the switcher and the toolbar buttons keep their
        room instead of being crowded together on narrow screens. */}
    <TitlePortal
      sx={{
        flex: 1,
        minWidth: 0,
        overflow: 'hidden',
        textOverflow: 'ellipsis',
        whiteSpace: 'nowrap',
        mr: 1,
      }}
    />
    <ScopeSelect />
  </AppBar>
);

export default ScopeAppBar;
