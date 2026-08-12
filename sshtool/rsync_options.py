"""RsyncOptions — 每次传输时可选择的 rsync 选项"""

from dataclasses import asdict, dataclass, field

from .config import EXCLUDE_PATTERNS


@dataclass
class RsyncOptions:
    """每次传输时可选择的 rsync 选项"""
    archive: bool = True
    verbose: bool = True
    compress: bool = True
    delete: bool = True
    progress: bool = True
    ignore_existing: bool = False

    exclude_git: bool = True
    exclude_build: bool = True
    exclude_install: bool = True
    exclude_cache: bool = True
    exclude_pycache: bool = True
    exclude_pyc: bool = True
    exclude_o: bool = True
    exclude_a: bool = True
    exclude_deb: bool = True
    exclude_zip: bool = True
    exclude_targz: bool = True
    exclude_compile_commands: bool = True

    custom_excludes: list = field(default_factory=list)

    # ---- 预设 ----

    @classmethod
    def preset_full(cls) -> "RsyncOptions":
        """完整同步（等同原脚本）"""
        return cls()

    @classmethod
    def preset_no_delete(cls) -> "RsyncOptions":
        """增量同步（有排除，无 --delete）"""
        return cls(delete=False)

    @classmethod
    def preset_minimal(cls) -> "RsyncOptions":
        """最简同步（仅 -avz，无 --delete，无排除）"""
        return cls(delete=False, **{attr: False for attr, _ in EXCLUDE_PATTERNS})

    # ---- 命令行生成 ----

    def to_args(self) -> list:
        """生成 rsync 命令行参数列表"""
        args = []
        if self.archive:
            args.append("-a")
        if self.verbose:
            args.append("-v")
        if self.compress:
            args.append("-z")
        if self.delete:
            args.append("--delete")
        if self.progress:
            args.append("--info=progress2")
        if self.ignore_existing:
            args.append("--ignore-existing")

        for attr, pattern in EXCLUDE_PATTERNS:
            if getattr(self, attr):
                args.extend(["--exclude", pattern])

        for pat in self.custom_excludes:
            if pat.strip():
                args.extend(["--exclude", pat.strip()])
        return args

    def to_dict(self) -> dict:
        d = asdict(self)
        d["custom_excludes"] = list(self.custom_excludes)
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "RsyncOptions":
        custom = d.pop("custom_excludes", [])
        return cls(**d, custom_excludes=list(custom))

    def summary(self) -> str:
        """简短摘要"""
        parts = [f for f, on in zip(
            ["-a", "-v", "-z", "--delete", "--ignore-existing"],
            [self.archive, self.verbose, self.compress,
             self.delete, self.ignore_existing],
        ) if on]
        if not parts:
            return "无选项"
        count = sum(getattr(self, attr) for attr, _ in EXCLUDE_PATTERNS)
        return " ".join(parts) + f" (排除{count}项)"
