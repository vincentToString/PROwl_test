from pydantic import BaseModel, Field
import os
class PullRequestData(BaseModel):
    action: str
    pr_number: int
    pr_title: str
    pr_body: str | None
    pr_url: str
    pr_diff_url: str
    pr_author: str
    repo_name: str
    repo_url: str
    created_at: str
    pr_diff_content: str | None = None


class Finding(BaseModel):
    severity: str
    title: str
    details: str
    file: str | None = None
    line: int | None = None

    def to_markdown(self) -> str:
        """Format finding as markdown for GitHub comments."""
        severity_emoji = {
            "critical": "🔴",
            "high": "🟠",
            "medium": "🟡",
            "low": "🔵",
            "info": "ℹ️"
        }
        emoji = severity_emoji.get(self.severity.lower(), "⚠️")

        markdown = f"### {emoji} {self.severity.upper()}: {self.title}\n\n"
        markdown += f"{self.details}\n\n"

        if self.file:
            location = f"`{self.file}`"
            if self.line:
                location += f" (Line {self.line})"
            markdown += f"**Location:** {location}\n"

        return markdown


class ReviewResult(BaseModel):
    review_id: str = Field(default_factory=lambda: os.urandom(8).hex())
    repo_name: str
    pr_number: int
    pr_url: str
    summary: str
    findings: list[Finding]
    guideline_references: list[str] = Field(
        default_factory=lambda: [
            "Avoid secrets in code",
            "Add/adjust tests when behavior changes",
        ]
    )
    llm_meta: dict = Field(default_factory=dict)

    def to_github_comment(self) -> str:
        """Format the review result as a comprehensive GitHub comment."""
        lines = []

        # Header
        lines.append("# 🦉 PROwl Code Review")
        lines.append(f"**Review ID:** `{self.review_id}`")
        lines.append("")

        # Summary section
        lines.append("## 📋 Summary")
        lines.append(self.summary)
        lines.append("")

        # Findings section
        if self.findings:
            # Group findings by severity
            severity_order = ["critical", "high", "medium", "low", "info"]
            findings_by_severity = {}
            for finding in self.findings:
                sev = finding.severity.lower()
                if sev not in findings_by_severity:
                    findings_by_severity[sev] = []
                findings_by_severity[sev].append(finding)

            lines.append("## 🔍 Findings")
            lines.append("")

            total = len(self.findings)
            severity_counts = {sev: len(findings_by_severity.get(sev, [])) for sev in severity_order}
            lines.append(f"**Total Issues Found:** {total}")

            # Show severity breakdown
            breakdown = " | ".join([f"{sev.capitalize()}: {count}" for sev, count in severity_counts.items() if count > 0])
            lines.append(f"**Breakdown:** {breakdown}")
            lines.append("")
            lines.append("---")
            lines.append("")

            # Output findings grouped by severity
            for severity in severity_order:
                if severity in findings_by_severity:
                    for finding in findings_by_severity[severity]:
                        lines.append(finding.to_markdown())
                        lines.append("---")
                        lines.append("")
        else:
            lines.append("## ✅ No Issues Found")
            lines.append("Great job! No significant issues were detected in this PR.")
            lines.append("")

        # Guidelines section
        if self.guideline_references:
            lines.append("## 📚 Guideline References")
            for guideline in self.guideline_references:
                lines.append(f"- {guideline}")
            lines.append("")

        # Metadata footer
        if self.llm_meta:
            lines.append("<details>")
            lines.append("<summary>🤖 Review Metadata</summary>")
            lines.append("")
            lines.append("```json")
            import json
            lines.append(json.dumps(self.llm_meta, indent=2))
            lines.append("```")
            lines.append("</details>")
            lines.append("")

        lines.append("---")
        lines.append("*Automated review powered by PROwl 🦉*")

        return "\n".join(lines)