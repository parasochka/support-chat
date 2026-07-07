import { useEffect, useState } from 'react';
import { AppBar, TitlePortal } from 'react-admin';
import Box from '@mui/material/Box';
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
      .then(({ json }) => setStructure(json))
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
      sx={{ mr: 2, minWidth: 180, '.MuiSelect-select': { py: 0.5 } }}
    >
      {items}
    </Select>
  );
};

const ScopeAppBar = () => (
  <AppBar>
    <TitlePortal />
    <Box flex={1} />
    <ScopeSelect />
  </AppBar>
);

export default ScopeAppBar;
