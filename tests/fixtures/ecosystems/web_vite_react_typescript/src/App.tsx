import "./app.css";

type AppProps = {
  projectName: string;
};

export function App({ projectName }: AppProps) {
  return <main aria-label="project-status">{projectName} ready</main>;
}
