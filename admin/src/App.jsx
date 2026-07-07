import {
  Admin,
  CustomRoutes,
  Layout,
  Menu,
  Resource,
  defaultDarkTheme,
  defaultLightTheme,
} from 'react-admin';
import { Route } from 'react-router-dom';
import AccountTreeIcon from '@mui/icons-material/AccountTree';
import DataObjectIcon from '@mui/icons-material/DataObject';
import ForumIcon from '@mui/icons-material/Forum';
import LibraryBooksIcon from '@mui/icons-material/LibraryBooks';
import PeopleIcon from '@mui/icons-material/People';
import PreviewIcon from '@mui/icons-material/Preview';
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
import PromptPreview from './pages/PromptPreview';
import PromptVariables from './pages/PromptVariables';
import Retention from './pages/Retention';
import Settings from './pages/Settings';
import Structure from './pages/Structure';
import Translations from './pages/Translations';

const AppMenu = () => (
  <Menu>
    <Menu.DashboardItem />
    <Menu.ResourceItems />
    <Menu.Item
      to="/prompt-preview"
      primaryText="Prompt preview"
      leftIcon={<PreviewIcon />}
    />
    <Menu.Item
      to="/prompt-variables"
      primaryText="Prompt variables"
      leftIcon={<TuneIcon />}
    />
    <Menu.Item
      to="/translations"
      primaryText="Translations"
      leftIcon={<TranslateIcon />}
    />
    <Menu.Item to="/settings" primaryText="Settings" leftIcon={<SettingsIcon />} />
    <Menu.Item to="/structure" primaryText="Structure" leftIcon={<AccountTreeIcon />} />
    <Menu.Item to="/retention" primaryText="Retention · TG" leftIcon={<SendIcon />} />
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
      icon={DataObjectIcon}
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
      <Route path="/prompt-preview" element={<PromptPreview />} />
      <Route path="/prompt-variables" element={<PromptVariables />} />
      <Route path="/translations" element={<Translations />} />
      <Route path="/settings" element={<Settings />} />
      <Route path="/structure" element={<Structure />} />
      <Route path="/retention" element={<Retention />} />
    </CustomRoutes>
  </Admin>
);

export default App;
