import { useEffect, useState } from 'react';
import {
  AppBar,
  LoadingIndicator,
  Logout,
  TitlePortal,
  useGetIdentity,
} from 'react-admin';
import { useNavigate } from 'react-router-dom';
import Divider from '@mui/material/Divider';
import IconButton from '@mui/material/IconButton';
import ListItemIcon from '@mui/material/ListItemIcon';
import ListSubheader from '@mui/material/ListSubheader';
import Menu from '@mui/material/Menu';
import MenuItem from '@mui/material/MenuItem';
import Select from '@mui/material/Select';
import Tooltip from '@mui/material/Tooltip';
import Typography from '@mui/material/Typography';
import AccountCircleIcon from '@mui/icons-material/AccountCircle';
import SettingsIcon from '@mui/icons-material/ManageAccounts';
import { API_URL, httpClient, getToken } from '../httpClient';
import { getPartnerId, getProductId, setScope } from '../productScope';
import { t } from '../i18n';

/**
 * User menu (identity + logout). React-admin's built-in UserMenu renders its
 * popover left-anchored in this version, so on both desktop and mobile the
 * "Logout" panel opened past the right edge of the screen and was clipped. This
 * drop-in right-anchors the Menu (its right edge meets the button's right edge)
 * and lets MUI clamp it into the viewport, so it always opens fully on-screen.
 */
const AppUserMenu = () => {
  const [anchor, setAnchor] = useState(null);
  const { identity } = useGetIdentity();
  const navigate = useNavigate();
  const close = () => setAnchor(null);
  return (
    <>
      <Tooltip title={t('Profile')}>
        <IconButton
          color="inherit"
          aria-haspopup="true"
          onClick={(e) => setAnchor(e.currentTarget)}
          sx={{ flexShrink: 0 }}
        >
          <AccountCircleIcon />
        </IconButton>
      </Tooltip>
      <Menu
        anchorEl={anchor}
        open={Boolean(anchor)}
        onClose={close}
        disableScrollLock
        anchorOrigin={{ vertical: 'bottom', horizontal: 'right' }}
        transformOrigin={{ vertical: 'top', horizontal: 'right' }}
        slotProps={{ paper: { sx: { minWidth: 200, maxWidth: '92vw' } } }}
      >
        {identity?.fullName && (
          <Typography
            variant="body2"
            color="text.secondary"
            sx={{ px: 2, py: 1, wordBreak: 'break-all' }}
          >
            {identity.fullName}
          </Typography>
        )}
        {identity?.fullName && <Divider />}
        <MenuItem
          onClick={() => {
            close();
            navigate('/account');
          }}
        >
          <ListItemIcon>
            <SettingsIcon fontSize="small" />
          </ListItemIcon>
          {t('Account & appearance')}
        </MenuItem>
        <Logout />
      </Menu>
    </>
  );
};

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
      setScope({ partnerId: id, name: pa ? `${pa.name} — ${t('all products')}` : '' });
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
      {t('All products')}
    </MenuItem>,
  ];
  (structure.partners || []).forEach((pa) => {
    items.push(
      <ListSubheader key={`h${pa.id}`}>{pa.name}</ListSubheader>,
      <MenuItem key={`pa${pa.id}`} value={`partner:${pa.id}`}>
        {pa.name} — {t('all')}
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
  // toolbar override: the default AppBar toolbar bundles a theme toggle (and the
  // locales menu we already dropped). Theme + language now live on the Account
  // page, so the header carries only the loading indicator — the toolbar stays
  // uncluttered and the phone tool-row never overflows. The user menu links to
  // the Account page for those controls.
  <AppBar userMenu={<AppUserMenu />} toolbar={<LoadingIndicator />}>
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
