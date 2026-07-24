import type { useConfigurationData } from '../../hooks/useConfigurationData';
import { AdapterSettings } from './AdapterSettings';
import { ApiKeysSettings } from './ApiKeysSettings';
import { AutonomySettings } from './AutonomySettings';
import { CliSettings } from './CliSettings';
import { ConfigurationPanel, type ConfigSection } from './ConfigurationPanel';
import { DangerSettings } from './DangerSettings';
import { McpSettings } from './McpSettings';
import { OrientationSettings, type OrientationMeasurement } from './OrientationSettings';
import { ProjectSettings } from './ProjectSettings';
import { SkillsSettings } from './SkillsSettings';
import { SystemSettings } from './SystemSettings';

type ConfigurationData = ReturnType<typeof useConfigurationData>;

interface ConfigurationWorkspaceProps {
  projectDisplayName: string;
  section: ConfigSection;
  onSectionChange: (section: ConfigSection) => void;
  configuration: ConfigurationData;
  health: { status?: string; mode?: string } | null;
  workspace: string;
  workspaceDraft: string;
  workspaceConfigured: boolean;
  loading: boolean;
  onWorkspaceDraftChange: (value: string) => void;
  onSaveWorkspace: () => Promise<void>;
  orientationMeasurement: OrientationMeasurement | null;
  orientationBusy: boolean;
  onOrientationConsentChange: (enabled: boolean) => Promise<void>;
  onEraseOrientation: () => Promise<void>;
  deleteConfirmation: string;
  onDeleteConfirmationChange: (value: string) => void;
  onDeleteProject: () => Promise<void>;
  lastResult: unknown;
  onRefresh: () => Promise<void>;
}

function shortProjectPath(path: string): string {
  return path.replaceAll('\\', '/').split('/').slice(-2).join('/');
}

export function ConfigurationWorkspace({
  projectDisplayName,
  section,
  onSectionChange,
  configuration,
  health,
  workspace,
  workspaceDraft,
  workspaceConfigured,
  loading,
  onWorkspaceDraftChange,
  onSaveWorkspace,
  orientationMeasurement,
  orientationBusy,
  onOrientationConsentChange,
  onEraseOrientation,
  deleteConfirmation,
  onDeleteConfirmationChange,
  onDeleteProject,
  lastResult,
  onRefresh,
}: ConfigurationWorkspaceProps) {
  const config = configuration;
  return (
    <ConfigurationPanel
      projectDisplayName={projectDisplayName}
      section={section}
      onSectionChange={onSectionChange}
    >
      {section === 'proyecto' && (
        <ProjectSettings
          health={health}
          workspace={workspace}
          workspaceDraft={workspaceDraft}
          loading={loading}
          onWorkspaceDraftChange={onWorkspaceDraftChange}
          onSaveWorkspace={onSaveWorkspace}
        />
      )}
      {section === 'autonomia' && (
        <AutonomySettings
          mode={config.autonomyMode}
          saving={config.autonomySaving}
          workspaceConfigured={workspaceConfigured}
          onSave={config.saveAutonomy}
        />
      )}
      {section === 'medicion' && (
        <OrientationSettings
          measurement={orientationMeasurement}
          busy={orientationBusy}
          onConsentChange={onOrientationConsentChange}
          onErase={onEraseOrientation}
        />
      )}
      {section === 'skills' && (
        <SkillsSettings
          governance={config.skillGovernance}
          skills={config.projectSkills}
          draft={config.skillDraft}
          saving={config.skillSaving}
          workspaceConfigured={workspaceConfigured}
          onDraftChange={config.setSkillDraft}
          onEdit={config.editProjectSkill}
          onToggle={config.toggleProjectSkill}
          onDelete={config.deleteProjectSkill}
          onSave={config.saveProjectSkill}
        />
      )}
      {section === 'mcp' && (
        <McpSettings
          catalog={config.mcpCatalog}
          servers={config.mcpServers}
          busyServer={config.mcpBusy}
          toolDrafts={config.mcpToolDrafts}
          onToolAccessChange={(serverName, toolName, access) => config.setMcpToolDrafts(
            (current) => ({
              ...current,
              [serverName]: { ...(current[serverName] || {}), [toolName]: access },
            }),
          )}
          onHealth={config.runMcpHealth}
          onSavePolicy={config.saveMcpToolPolicy}
          onTransition={config.transitionMcpServer}
        />
      )}
      {section === 'danger' && (
        <DangerSettings
          projectLabel={workspace ? shortProjectPath(workspace) : ''}
          confirmation={deleteConfirmation}
          busy={loading}
          onConfirmationChange={onDeleteConfirmationChange}
          onDelete={onDeleteProject}
        />
      )}
      {section === 'keys' && (
        <ApiKeysSettings
          secrets={config.secrets}
          activeProvider={config.secretProvider}
          secretValue={config.secretValue}
          busy={loading}
          onProviderChange={config.setSecretProvider}
          onSecretChange={config.setSecretValue}
          onSave={config.saveSecret}
        />
      )}
      {section === 'clis' && (
        <CliSettings items={config.cliStatus} busy={loading} onLogin={config.launchCliLogin} />
      )}
      {section === 'adapters' && (
        <AdapterSettings
          rows={config.adapterProfiles.map((profile) => ({
            profile,
            connected: config.profileState(profile).connected,
            testModel: config.adapterTestModels[profile.id]
              || String(profile.config?.model || profile.model_options?.[0]?.value || ''),
          }))}
          busy={loading}
          onModelChange={(profileId, model) => config.setAdapterTestModels(
            (current) => ({ ...current, [profileId]: model }),
          )}
          onTest={config.testAdapterProfile}
        />
      )}
      {section === 'sistema' && (
        <SystemSettings
          draftRoot={config.settingsDraft}
          effectiveRoot={config.projectsRoot}
          backendOrigin={window.location.origin}
          mode={health?.mode}
          lastResult={lastResult}
          busy={loading}
          onDraftChange={config.setSettingsDraft}
          onSave={async () => {
            await config.saveAppSettings();
            await onRefresh();
          }}
        />
      )}
    </ConfigurationPanel>
  );
}
