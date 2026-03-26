"""Process lifecycle management for the launcher."""

from PySide6.QtCore import QObject, QProcess, QProcessEnvironment, Signal

from mc_common import format_cmd


class ProcessManager(QObject):
    """Manages QProcess instances for main tasks and server processes.

    Emits signals so the UI can react to output, completion, and busy state.
    """

    main_output = Signal(str)
    main_started = Signal(str)  # command line string
    main_finished = Signal(int)  # exit_code
    server_output = Signal(str, str)  # instance_key, text
    server_started = Signal(str, str)  # instance_key, command line string
    server_finished = Signal(str, int)  # instance_key, exit_code

    def __init__(self, working_dir, parent=None):
        super().__init__(parent)
        self._working_dir = str(working_dir)
        self.server_processes = {}

        # Main process (single, shared)
        self.main_process = QProcess(self)
        self.main_process.setProcessChannelMode(QProcess.MergedChannels)
        self.main_process.readyReadStandardOutput.connect(self._on_main_output)
        self.main_process.finished.connect(self._on_main_finished)

    def is_main_busy(self):
        return self.main_process.state() != QProcess.NotRunning

    def start_main(self, args):
        """Start a main process. Returns False if already busy."""
        if self.is_main_busy():
            return False

        cmd_line = format_cmd([str(a) for a in args])
        self.main_started.emit(cmd_line)

        env = QProcessEnvironment.systemEnvironment()
        env.insert("PYTHONUNBUFFERED", "1")
        self.main_process.setProcessEnvironment(env)
        self.main_process.setWorkingDirectory(self._working_dir)
        self.main_process.start(args[0], [str(a) for a in args[1:]])
        return True

    def start_server(self, args, instance_key):
        """Start a server process. Returns False if already running."""
        if instance_key in self.server_processes:
            proc = self.server_processes[instance_key]
            if proc.state() != QProcess.NotRunning:
                return False

        cmd_line = format_cmd([str(a) for a in args])
        self.server_started.emit(instance_key, cmd_line)

        proc = QProcess(self)
        proc.setProcessChannelMode(QProcess.MergedChannels)
        proc.readyReadStandardOutput.connect(
            lambda key=instance_key: self._on_server_output(key)
        )
        proc.readyReadStandardError.connect(
            lambda key=instance_key: self._on_server_output(key)
        )
        proc.finished.connect(
            lambda code, status, key=instance_key: self._on_server_finished(key, code)
        )
        self.server_processes[instance_key] = proc

        env = QProcessEnvironment.systemEnvironment()
        env.insert("PYTHONUNBUFFERED", "1")
        proc.setProcessEnvironment(env)
        proc.setWorkingDirectory(self._working_dir)
        proc.start(args[0], [str(a) for a in args[1:]])
        return True

    def stop_server(self, instance_key):
        """Stop a running server process."""
        proc = self.server_processes.get(instance_key)
        if proc and proc.state() != QProcess.NotRunning:
            from core.platform import kill_process_tree
            pid = proc.processId()
            if pid:
                kill_process_tree(pid)
            else:
                proc.kill()

    def is_server_running(self, instance_key):
        proc = self.server_processes.get(instance_key)
        return proc is not None and proc.state() != QProcess.NotRunning

    def running_servers(self):
        """Return list of instance_keys with running servers."""
        return [k for k, p in self.server_processes.items()
                if p.state() != QProcess.NotRunning]

    def cleanup(self):
        """Kill all running processes (called on window close)."""
        from core.platform import kill_process_tree
        if self.main_process.state() != QProcess.NotRunning:
            pid = self.main_process.processId()
            if pid:
                kill_process_tree(pid)
            else:
                self.main_process.kill()
            self.main_process.waitForFinished(3000)
        for proc in self.server_processes.values():
            if proc.state() != QProcess.NotRunning:
                pid = proc.processId()
                if pid:
                    kill_process_tree(pid)
                else:
                    proc.kill()
                proc.waitForFinished(3000)

    # ── Internal signal handlers ──────────────────────────────

    def _on_main_output(self):
        data = bytes(self.main_process.readAllStandardOutput()).decode(
            "utf-8", errors="replace"
        )
        if data:
            self.main_output.emit(data.rstrip())

    def _on_main_finished(self, exit_code, _status):
        self.main_finished.emit(exit_code)

    def _on_server_output(self, instance_key):
        proc = self.server_processes.get(instance_key)
        if proc:
            data = bytes(proc.readAllStandardOutput()).decode(
                "utf-8", errors="replace"
            )
            if data:
                self.server_output.emit(instance_key, data.rstrip())

    def _on_server_finished(self, instance_key, exit_code):
        if instance_key in self.server_processes:
            self.server_processes[instance_key].deleteLater()
            del self.server_processes[instance_key]
        self.server_finished.emit(instance_key, exit_code)
