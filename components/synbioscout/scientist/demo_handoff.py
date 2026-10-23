"""Input checklist reporting file presence and the recorded preparation status."""
from __future__ import annotations

from pathlib import Path

from .models import TargetPackage

MODEL_INPUTS = {
    "drugclip": {
        "label": "DrugCLIP", "inputs": ["protein_pdb", "reference_ligand_sdf"],
        "contract": "准备好的蛋白与同坐标系参考配体，配体周围6 Å定义口袋。",
        "next_step": "为本次口袋重新编码；分子embedding须与权重、预处理和ID映射一致。",
    },
    "sprint": {
        "label": "SPRINT", "inputs": ["protein_pdb"],
        "contract": "输入为已解析的完整蛋白链；SaProt 接受长度不超过1022残基的序列。",
        "next_step": "为本次蛋白链生成匹配的 Foldseek/SaProt 特征与靶点向量。",
    },
    "psichic": {
        "label": "PSICHIC", "inputs": ["sequence_fasta"],
        "contract": "与研究对象一致的一条蛋白序列，完整蛋白或明确选择的结构域。",
        "next_step": "为本次序列重新生成蛋白图，并核对分子图与模型环境。",
    },
    "pharmaconet": {
        "label": "PharmacoNet", "inputs": ["protein_pdb", "reference_ligand_sdf"],
        "contract": "准备好的蛋白与同坐标系参考配体，用参考配体定位口袋。",
        "next_step": "为本次口袋生成药效团；构象状态与可用评分分别记录。",
    },
}


def target_screening_checklist(package: TargetPackage, project_root: Path) -> dict:
    """Report recorded readiness plus scoped file presence, without changing history.

    This check reads file presence. Coordinates, sequence, ligand identity,
    model weights and cached features retain their separate validation status.
    """
    root = Path(project_root).resolve()
    files = []
    available = {}
    for kind in ("protein_pdb", "sequence_fasta", "reference_ligand_sdf"):
        value = getattr(package, kind)
        path = Path(value).resolve() if value and Path(value).is_absolute() else None
        scoped = path is not None and path.is_relative_to(root)
        present = bool(scoped and path.is_file())
        available[kind] = present
        files.append({"kind": kind, "present": present,
                      "state": "present" if present else "missing_or_outside_project"})
    models = []
    for key, contract in MODEL_INPUTS.items():
        readiness = package.model_readiness.get(key, {})
        recorded = readiness.get("ready") is True
        missing = [kind for kind in contract["inputs"] if not available[kind]]
        input_state = "recorded_ready_files_present" if (
            recorded and not missing and package.status == "ready"
        ) else "needs_review"
        models.append({"id": key, **contract, "recorded_ready": recorded,
                       "input_state": input_state, "missing_inputs": missing,
                       "features": "not_checked", "execution": "not_run"})
    return {
        "schema_version": 1, "package_id": package.package_id,
        "recorded_package_status": package.status,
        "scope": "recorded_readiness_and_local_file_presence_only",
        "files": files, "models": models,
        "all_inputs_recorded_ready": all(
            m["input_state"] == "recorded_ready_files_present" for m in models
        ),
        "demo_cache_compatibility": "not_checked",
        "limitations": [
            "输入就绪、模型环境、权重、缓存与评分执行状态分别记录。",
            "本检查读取文件存在状态；文件内容变更后重新准备并审查。",
            "HDAC2 缓存对应其构建体、链、序列、口袋和版本；新靶点使用匹配的新缓存。",
            "ADMET、ChassisGate、对接和MD保持独立阶段，未因本检查而执行。",
            "文献候选与靶点筛选分别保留各自的作用机制和靶点关系。",
        ],
    }

