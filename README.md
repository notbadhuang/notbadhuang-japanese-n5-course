# 日语0到N5课程

这是面向电脑端学习的日语0到N5课程公开发行仓库。课程通过WorkBuddy中的“日语0到N5课程”Skill安装和运行；Skill会从本仓库的固定GitHub Release下载课程ZIP，并在安装前核对课程身份、文件大小和SHA-256。

本仓库与 `v0.1.0-rc.1` 课程Release现已作为公开预发布提供。SkillHub条目和Windows负责人验收尚未完成，因此仍不应把当前状态描述为正式版、种子测试或付费版本。

## 仓库边界

- 本仓库仅包含发行允许清单内的课程、播放器、音频、必要许可证和公开说明。
- 研发来源、内部审计、风险说明、设计稿、候选材料和学员档案不在本仓库中。
- 学习Skill在独立仓库 `nutletor/japanese-n5-course-skill` 维护。
- `dist/`中的ZIP和发布清单用于创建GitHub Release；ZIP不提交到Git历史。

## 答案与评分

课程为完全离线运行，因此答案、听力脚本和本地评分实现也会随仓库公开，并集中放在`assessment-data`等课程数据中。正常学习界面仍只会在提交答案或完成相应阶段后显示反馈；本仓库不对答案作保密承诺。

## 许可

本项目完全自有且明确纳入授权范围的课程内容、文档和代码采用CC BY-NC 4.0。第三方素材不被该许可覆盖，继续遵守各自许可证，详见`LICENSE.md`、`THIRD_PARTY_NOTICES.md`、`THIRD_PARTY_DATA/`与`THIRD_PARTY_LICENSES/`。
