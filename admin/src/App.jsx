import {
  Admin,
  CustomRoutes,
  Layout,
  Menu,
  Resource,
  defaultDarkTheme,
  defaultLightTheme,
} from 'react-admin';
import { Navigate, Route } from 'react-router-dom';
import Typography from '@mui/material/Typography';
import AccountTreeIcon from '@mui/icons-material/AccountTree';
import ForumIcon from '@mui/icons-material/Forum';
import LibraryBooksIcon from '@mui/icons-material/LibraryBooks';
import PeopleIcon from '@mui/icons-material/People';
import ReportProblemIcon from '@mui/icons-material/ReportProblem';
import SendIcon from '@mui/icons-material/Send';
import SettingsIcon from '@mui/icons-material/Settings';
import TranslateIcon from '@mui/icons-material/Translate';
import TuneIcon from '@mui/icons-material/Tune';

import authProvider from './authProvider';
import dataProvider from './dataProvider';
import Dashboard from './dashboard/Dashboard';
import ScopeAppBar from './layout/ScopeAppBar';
import { ConversationList, ConversationShow } from './resources/Conversations';
import { EscalationList } from './resources/Escalations';
import { KbCreate, KbEdit, KbList } from './resources/KnowledgeBase';
import { KbVariableEdit, KbVariableList } from './resources/KbVariables';
import { UserCreate, UserEdit, UserList } from './resources/Users';
import Prompt from './pages/Prompt';
import Retention from './pages/Retention';
import Settings from './pages/Settings';
import Structure from './pages/Structure';
import Translations from './pages/Translations';

const MenuSection = ({ label }) => (
  <Typography
    variant="overline"
    color="text.secondary"
    sx={{ px: 2, pt: 2, pb: 0.5, display: 'block', lineHeight: 1.5 }}
  >
    {label}
  </Typography>
);

/**
 * The sidebar in three sections: the support-chat surface, the Telegram
 * retention bot, and the system-wide management. KB variables ride inside the
 * Knowledge base page (a tab there), so they get no menu item of their own.
 */
const AppMenu = () => (
  <Menu>
    <Menu.DashboardItem />

    <MenuSection label="Support chat" />
    <Menu.ResourceItem name="sessions" />
    <Menu.ResourceItem name="unresolved" />
    <Menu.ResourceItem name="kb" />
    <Menu.Item to="/prompt" primaryText="Prompt" leftIcon={<TuneIcon />} />
    <Menu.Item
      to="/translations"
      primaryText="Translations"
      leftIcon={<TranslateIcon />}
    />

    <MenuSection label="Telegram · Retention" />
    <Menu.Item
      to="/retention"
      primaryText="Retention bot"
      leftIcon={<SendIcon />}
    />

    <MenuSection label="System" />
    <Menu.Item to="/structure" primaryText="Structure" leftIcon={<AccountTreeIcon />} />
    <Menu.Item to="/settings" primaryText="Settings" leftIcon={<SettingsIcon />} />
    <Menu.ResourceItem name="users" />
  </Menu>
);

const AppLayout = ({ children }) => (
  <Layout menu={AppMenu} appBar={ScopeAppBar}>
    {children}
  </Layout>
);

const App = () => (
  <Admin
    dataProvider={dataProvider}
    authProvider={authProvider}
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
      options={{ label: 'Conversations' }}
      list={ConversationList}
      show={ConversationShow}
      icon={ForumIcon}
    />
    <Resource
      name="unresolved"
      options={{ label: 'Escalations' }}
      list={EscalationList}
      icon={ReportProblemIcon}
    />
    <Resource
      name="kb"
      options={{ label: 'Knowledge base' }}
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
      options={{ label: 'Users' }}
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
      <Route path="/settings" element={<Settings />} />
      <Route path="/structure" element={<Structure />} />
      <Route path="/retention" element={<Retention />} />
    </CustomRoutes>
  </Admin>
);

export default App;
