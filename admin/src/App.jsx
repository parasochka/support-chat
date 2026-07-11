import { Suspense, lazy, useState } from 'react';
import {
  Admin,
  CustomRoutes,
  Layout,
  Menu,
  Resource,
  defaultDarkTheme,
  defaultLightTheme,
  usePermissions,
} from 'react-admin';
import { Navigate, Route, useLocation, useNavigate } from 'react-router-dom';
import Box from '@mui/material/Box';
import CircularProgress from '@mui/material/CircularProgress';
import Collapse from '@mui/material/Collapse';
import ListItemButton from '@mui/material/ListItemButton';
import ListItemIcon from '@mui/material/ListItemIcon';
import ListItemText from '@mui/material/ListItemText';
import AccountTreeIcon from '@mui/icons-material/AccountTree';
import SmartToyIcon from '@mui/icons-material/SmartToy';
import ExpandLess from '@mui/icons-material/ExpandLess';
import ExpandMore from '@mui/icons-material/ExpandMore';
import ForumIcon from '@mui/icons-material/Forum';
import VpnKeyIcon from '@mui/icons-material/VpnKey';
import InsightsIcon from '@mui/icons-material/Insights';
import LibraryBooksIcon from '@mui/icons-material/LibraryBooks';
import LinkIcon from '@mui/icons-material/Link';
import PeopleIcon from '@mui/icons-material/People';
import PhotoLibraryIcon from '@mui/icons-material/PhotoLibrary';
import ReportProblemIcon from '@mui/icons-material/ReportProblem';
import SettingsIcon from '@mui/icons-material/Settings';
import SupportAgentIcon from '@mui/icons-material/SupportAgent';
import TelegramIcon from '@mui/icons-material/Telegram';
import TranslateIcon from '@mui/icons-material/Translate';
import TuneIcon from '@mui/icons-material/Tune';

import polyglotI18nProvider from 'ra-i18n-polyglot';
import englishMessages from 'ra-language-english';
import russianMessages from 'ra-language-russian';

import authProvider from './authProvider';
import dataProvider from './dataProvider';
import buildStore from './store';
import LoginPage from './pages/LoginPage';
import ScopeAppBar from './layout/ScopeAppBar';
import { ConversationList, ConversationShow } from './resources/Conversations';
import { EscalationList } from './resources/Escalations';
import { KbCreate, KbEdit, KbList } from './resources/KnowledgeBase';
import { KbVariableEdit, KbVariableList } from './resources/KbVariables';
import { UserCreate, UserEdit, UserList } from './resources/Users';
import { getAdminLang, t } from './i18n';

// React-admin's own chrome (list toolbars, pagination, login, confirm dialogs)
// follows the same persisted EN/RU choice as the custom pages' t() dictionary.
const i18nProvider = polyglotI18nProvider(
  (locale) => (locale === 'ru' ? russianMessages : englishMessages),
  getAdminLang(),
  [
    { locale: 'en', name: 'English' },
    { locale: 'ru', name: 'Русский' },
  ],
  { allowMissing: true }
);

// ---------------------------------------------------------------------------
// Code splitting: every heavy page loads on demand (React.lazy → its own
// chunk), so the initial bundle carries only the shell + the list resources.
// Dashboard and Retention pull recharts; lazy-loading them moves the chart
// library out of the entry chunk entirely.
// ---------------------------------------------------------------------------
const PageFallback = () => (
  <Box sx={{ display: 'flex', justifyContent: 'center', p: 6 }}>
    <CircularProgress size={28} />
  </Box>
);

const lazyPage = (importer) => {
  const C = lazy(importer);
  const Wrapped = (props) => (
    <Suspense fallback={<PageFallback />}>
      <C {...props} />
    </Suspense>
  );
  return Wrapped;
};

const Dashboard = lazyPage(() => import('./dashboard/Dashboard'));
const ApiKeys = lazyPage(() => import('./pages/ApiKeys'));
const Prompt = lazyPage(() => import('./pages/Prompt'));
const Retention = lazyPage(() => import('./pages/Retention'));
const RetentionAgent = lazyPage(() => import('./pages/RetentionAgent'));
const Settings = lazyPage(() => import('./pages/Settings'));
const SiteMap = lazyPage(() => import('./pages/SiteMap'));
const Structure = lazyPage(() => import('./pages/Structure'));
const Translations = lazyPage(() => import('./pages/Translations'));

// One store instance for the app: a localStorage store whose reset() preserves
// the theme + sidebar preference across logout (see ./store).
const store = buildStore();

// Collapsed/expanded state of the sidebar sections persists across reloads so
// the operator's layout is remembered (default: expanded).
const OPEN_KEY = 'admin_menu_sections';
const loadOpen = () => {
  try {
    return JSON.parse(localStorage.getItem(OPEN_KEY)) || {};
  } catch {
    return {};
  }
};
const saveOpen = (state) => localStorage.setItem(OPEN_KEY, JSON.stringify(state));

const CollapsibleSection = ({ id, label, children }) => {
  const [open, setOpen] = useState(() => loadOpen()[id] !== false);
  const toggle = () => {
    const next = !open;
    setOpen(next);
    saveOpen({ ...loadOpen(), [id]: next });
  };
  return (
    <>
      <ListItemButton onClick={toggle} sx={{ px: 2, py: { xs: 1, md: 0.5 } }}>
        <ListItemText
          primary={label}
          slotProps={{
            primary: { variant: 'overline', color: 'text.secondary' },
          }}
        />
        {open ? <ExpandLess fontSize="small" /> : <ExpandMore fontSize="small" />}
      </ListItemButton>
      <Collapse in={open} timeout="auto" unmountOnExit>
        {/* Every child of a section is a nested entry — indent uniformly so the
            hierarchy reads at a glance (matches the Retention sub-items). */}
        <Box sx={{ '& .MuiMenuItem-root, & .MuiListItemButton-root': { pl: 4 } }}>
          {children}
        </Box>
      </Collapse>
    </>
  );
};

// A generic sidebar sub-entry (ListItemButton — the SAME renderer for every
// custom entry, so no item can drift out of line with its siblings). `to` is a
// path (+ optional query); `active` is an exact matcher over the location so
// e.g. /retention-agent can never light up a /retention?tab=… sibling.
const SubItem = ({ to, label, icon, active }) => {
  const navigate = useNavigate();
  const location = useLocation();
  return (
    <ListItemButton
      selected={active(location)}
      onClick={() => navigate(to)}
      sx={{ pl: 4, py: { xs: 1, md: 0.4 } }}
    >
      <ListItemIcon sx={{ minWidth: 34 }}>{icon}</ListItemIcon>
      {/* No typography override: the Menu-level rule below pins one font size
          for every entry, so sub-items match the resource/custom items. */}
      <ListItemText primary={label} />
    </ListItemButton>
  );
};

// Two retention tabs are bundled under a parent's sidebar entry (like the
// Support "Prompt" page): the Setup guide lives under Telegram config, and the
// Prompt variables under Prompt. So those parent entries stay highlighted while
// their sub-tab is active.
const RETENTION_TAB_PARENT = { guide: 'config', variables: 'prompt' };

// A retention sub-tab as its own sidebar entry: navigates to /retention?tab=…
// and highlights when that tab (or one bundled under it) is the active one (the
// page reads ?tab=). The pathname match is EXACT — '/retention-agent' also
// startsWith '/retention', which used to light up "Telegram config" whenever
// the Proactive agent page was open.
const RetentionSubItem = ({ tab, label, icon }) => (
  <SubItem
    to={`/retention?tab=${tab}`}
    label={label}
    icon={icon}
    active={(location) => {
      if (location.pathname !== '/retention') return false;
      const current = new URLSearchParams(location.search).get('tab') || 'config';
      return (RETENTION_TAB_PARENT[current] || current) === tab;
    }}
  />
);

// A settings surface entry: /settings?module=… — matched on the module param
// (three sidebar sections each link their own module of the same page).
const SettingsSubItem = ({ module, label }) => (
  <SubItem
    to={`/settings?module=${module}`}
    label={label}
    icon={<SettingsIcon fontSize="small" />}
    active={(location) => {
      if (location.pathname !== '/settings') return false;
      const m = new URLSearchParams(location.search).get('module') || 'core';
      return m === module;
    }}
  />
);

/**
 * The sidebar in three collapsible sections: the support-chat surface, the
 * Telegram retention bot (whose sub-tabs are exposed as sub-menu entries), and
 * system-wide management. Section open/closed state is remembered. KB variables
 * ride inside the Knowledge base page (a tab there), so they get no menu item.
 */
const AppMenu = () => {
  // API keys are credentials — the entry is admin-only (the server refuses
  // managers anyway; hiding it mirrors that, like the page itself does).
  const { permissions } = usePermissions();
  return (
    // Sidebar entries come from three different sources whose default typography
    // differs, so they must be normalized to ONE look. The catch: RA's
    // Menu.ResourceItem / Menu.Item (MenuItemLink) render the label as a bare
    // <Typography variant="inherit"> directly inside .MuiMenuItem-root — NOT a
    // ListItemText — while the custom sub-items (ListItemButton) render a
    // .MuiListItemText-primary. A rule that targets only one of those leaves the
    // column two-toned. So every rule below is written to cover BOTH the MenuItem
    // label and the ListItemText label. Section headers keep their overline
    // style (excluded via :not()).
    <Menu
      sx={{
        '& .MuiListItemIcon-root .MuiSvgIcon-root': { fontSize: 20 },
        // One icon size + inactive colour for every entry source.
        '& .MuiListItemIcon-root': { minWidth: 34, color: 'text.secondary' },
        // One inactive label size + colour. .MuiMenuItem-root covers the RA
        // links (label is an inheriting Typography), .MuiListItemText-primary
        // covers the custom sub-items; both to text.secondary so the whole
        // inactive column is a single tone (RA's default, matching Dashboard).
        '& .MuiMenuItem-root': { fontSize: '0.875rem', color: 'text.secondary' },
        '& .MuiListItemText-primary:not(.MuiTypography-overline)': {
          fontSize: '0.875rem',
          color: 'text.secondary',
        },
        // Active/selected entry — same accent for every source. RA marks the
        // active link with .RaMenuItemLink-active on the MenuItem (its inheriting
        // label picks up the colour); the custom sub-item uses .Mui-selected
        // on the ListItemButton (colour pinned on its ListItemText). Cover both,
        // label + icon, so the highlight reads identically everywhere.
        '& .RaMenuItemLink-active': { color: 'primary.main', fontWeight: 600 },
        '& .Mui-selected .MuiListItemText-primary:not(.MuiTypography-overline)': {
          color: 'primary.main',
          fontWeight: 600,
        },
        '& .RaMenuItemLink-active .MuiListItemIcon-root, & .Mui-selected .MuiListItemIcon-root':
          { color: 'primary.main' },
      }}
    >
      <Menu.DashboardItem primaryText={t('Dashboard')} />

      <CollapsibleSection id="support" label={t('Support chat')}>
        <Menu.ResourceItem name="sessions" />
        <Menu.ResourceItem name="unresolved" />
        <Menu.ResourceItem name="kb" />
        <Menu.Item to="/site-map" primaryText={t('Site map')} leftIcon={<LinkIcon />} />
        <Menu.Item to="/prompt" primaryText={t('Prompt')} leftIcon={<TuneIcon />} />
        <Menu.Item
          to="/translations"
          primaryText={t('Translations')}
          leftIcon={<TranslateIcon />}
        />
        <SettingsSubItem module="support" label={t('Chat settings')} />
        {/* The combined dashboard narrowed to the support block. */}
        <Menu.Item
          to="/?module=support"
          primaryText={t('Analytics')}
          leftIcon={<InsightsIcon />}
        />
      </CollapsibleSection>

      <CollapsibleSection id="retention" label={t('Telegram · Retention')}>
        {/* Setup guide is a sub-tab of Telegram config; Prompt variables a
            sub-tab of Prompt — so neither gets its own sidebar entry. */}
        <RetentionSubItem tab="config" label={t('Telegram config')} icon={<TelegramIcon fontSize="small" />} />
        <RetentionSubItem tab="kb" label={t('Retention KB')} icon={<LibraryBooksIcon fontSize="small" />} />
        <RetentionSubItem tab="prompt" label={t('Prompt')} icon={<TuneIcon fontSize="small" />} />
        <RetentionSubItem tab="photos" label={t('Media')} icon={<PhotoLibraryIcon fontSize="small" />} />
        <RetentionSubItem tab="managers" label={t('Managers')} icon={<SupportAgentIcon fontSize="small" />} />
        {/* The proactive agent — the event-driven regime that writes first.
            Rendered by the SAME SubItem as its siblings so it aligns with them
            (the old Menu.Item carried different padding and sat out of line). */}
        <SubItem
          to="/retention-agent"
          label={t('Proactive agent')}
          icon={<SmartToyIcon fontSize="small" />}
          active={(location) =>
            location.pathname === '/retention-agent' ||
            location.pathname === '/retention-v2'
          }
        />
        <SettingsSubItem module="retention" label={t('Bot settings')} />
        <RetentionSubItem tab="chats" label={t('Conversations')} icon={<ForumIcon fontSize="small" />} />
        <RetentionSubItem tab="analytics" label={t('Analytics')} icon={<InsightsIcon fontSize="small" />} />
      </CollapsibleSection>

      <CollapsibleSection id="system" label={t('System')}>
        <Menu.Item to="/structure" primaryText={t('Structure')} leftIcon={<AccountTreeIcon />} />
        <SettingsSubItem module="core" label={t('Settings')} />
        {/* User management is admin-only server-side (403 for managers) —
            hide the entry instead of showing a dead link. */}
        {permissions === 'admin' && <Menu.ResourceItem name="users" />}
        {permissions === 'admin' && (
          <Menu.Item to="/api-keys" primaryText={t('API keys')} leftIcon={<VpnKeyIcon />} />
        )}
      </CollapsibleSection>
    </Menu>
  );
};

const AppLayout = ({ children }) => (
  <Layout menu={AppMenu} appBar={ScopeAppBar}>
    {children}
  </Layout>
);

const App = () => (
  <Admin
    dataProvider={dataProvider}
    authProvider={authProvider}
    i18nProvider={i18nProvider}
    store={store}
    loginPage={LoginPage}
    dashboard={Dashboard}
    layout={AppLayout}
    theme={defaultLightTheme}
    darkTheme={defaultDarkTheme}
    defaultTheme="dark"
    title="Support Chat Admin"
    disableTelemetry
  >
    <Resource
      name="sessions"
      options={{ label: t('Conversations') }}
      list={ConversationList}
      show={ConversationShow}
      icon={ForumIcon}
    />
    <Resource
      name="unresolved"
      options={{ label: t('Escalations') }}
      list={EscalationList}
      icon={ReportProblemIcon}
    />
    <Resource
      name="kb"
      options={{ label: t('Knowledge base') }}
      list={KbList}
      edit={KbEdit}
      create={KbCreate}
      icon={LibraryBooksIcon}
    />
    <Resource
      name="kb_variables"
      options={{ label: 'KB variables' }}
      list={KbVariableList}
      edit={KbVariableEdit}
    />
    <Resource
      name="users"
      options={{ label: t('Users') }}
      list={UserList}
      edit={UserEdit}
      create={UserCreate}
      icon={PeopleIcon}
    />
    <CustomRoutes>
      <Route path="/prompt" element={<Prompt />} />
      {/* Legacy deep links from the previous menu layout. */}
      <Route path="/prompt-preview" element={<Navigate to="/prompt" replace />} />
      <Route
        path="/prompt-variables"
        element={<Navigate to="/prompt?tab=variables" replace />}
      />
      <Route path="/translations" element={<Translations />} />
      <Route path="/site-map" element={<SiteMap />} />
      <Route path="/settings" element={<Settings />} />
      <Route path="/structure" element={<Structure />} />
      <Route path="/retention" element={<Retention />} />
      <Route path="/retention-agent" element={<RetentionAgent />} />
      {/* Legacy bookmark: the old Retention v2 path lands on the agent page. */}
      <Route path="/retention-v2" element={<RetentionAgent />} />
      <Route path="/api-keys" element={<ApiKeys />} />
    </CustomRoutes>
  </Admin>
);

export default App;
