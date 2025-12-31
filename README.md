# Aiwen Python 后端

## 介绍

该文档用于向团队成员说明如何使用 Git 来更新 Aiwen后端python项目。
包括如何创建项目、添加文件、推送更新，运行该项目等。

## 克隆项目

- [创建gitlab账号](https://docs.gitlab.com/18.0/user/profile/)
- [获取token](https://docs.gitlab.com/security/tokens/)
- [配置ssh](https://docs.gitlab.com/18.0/user/ssh/)
- [克隆项目](https://juejin.cn/post/7493037770954162214)

### 使用ssh克隆

```shell
git clone ssh://git@10.1.2.109:2225/teamai/ai630.git
```

### 使用https克隆

```shell
git clone http://10.1.2.109/teamai/ai630.git
```

## 提交更新

- [使用pycharm提交更新](https://www.jetbrains.com/zh-cn/help/pycharm/commit-and-push-changes.html#commit)
- [使用git命令提交更新](https://www.runoob.com/git/git-commit.html)

## 启动 Agent Worker

项目包含一个后台 Worker 用于处理异步任务。要启动 Worker，请参考 [Worker Setup 文档](docs/worker_setup.md)。

简单启动方法：
```bash
# 开发环境启动
./scripts/start_worker.sh

# 或者直接使用 Python 模块
python -m src.aiwen.workers
```

### Commit建议与规范

详情可参考 [Git Commit Message 规范](https://www.conventionalcommits.org/zh-hans/v1.0.0/)

| 类型（type）   | 说明                                      | 示例                            |
|------------|-----------------------------------------|-------------------------------|
| `feat`     | ✨ 新功能、新特性                               | `feat(auth): 添加用户登录接口`        |
| `fix`      | 🐛 修复 bug                               | `fix(api): 修复 token 校验逻辑`     |
| `docs`     | 📝 仅文档变更（如修改 README、注释等）                | `docs(readme): 补充部署说明`        |
| `style`    | 💅 格式变动（无功能影响，如空格、缩进、分号）                | `style(ui): 调整按钮样式格式`         |
| `refactor` | 🔨 代码重构（非新增功能或修复 bug）                   | `refactor(core): 提取公共方法`      |
| `perf`     | ⚡ 性能优化                                  | `perf(query): 提升数据查询速度`       |
| `test`     | ✅ 添加或修改测试代码                             | `test(user): 增加登录测试用例`        |
| `build`    | 📦 构建系统或外部依赖变更（如 Webpack、npm）           | `build(deps): 升级 axios 至 1.5` |
| `ci`       | 🔁 持续集成配置变更（如 GitHub Actions、GitLab CI） | `ci(workflow): 修复 CI 构建失败问题`  |
| `chore`    | 🧹 杂项变更（不属于上述类型，如更新 .gitignore）         | `chore: 更新 .gitignore 文件`     |
| `revert`   | ⏪ 回滚某个提交                                | `revert: 回滚 fix(token) 的改动`   |

### 推送更新到远程

#### 推送前注意事项

- [ ] 检查环境变量中是否有可以重复的环境变量，在设置环境变量是先检查是否已存在
- [ ] 编写[测试用例](tests)运行pytest测试用例，确保所有测试用例通过，确保没有错误
    ```shell
    pytest
    ```

- [ ] 检查[Config文件](src/aiwen/config/middleware_config.py)
  避免多次创建AppConfig

- [ ] 检查本地git缓存，确保不包含已被添加在gitignore中的文件
   ```shell
   git ls-files -i -c --exclude-standard
   ``` 
    - 如果存在则执行以下命令更改
    ```shell
   git rm -r --cached <file_or_directory>
    ```
  这里的 <file_or_directory> 是你想要从 Git 缓存中移除的文件或文件夹。如果是所有被忽略的文件，你可以使用 . 来递归移除所有文件：
    ```shell
  git rm -r --cached .
  git add .
    ```
  提交更改:
    ```shell
  git commit -m "Remove ignored files from cache"
    ```

### 复杂更新（建议步骤）

1. [从 dev 分支拉出 feature 分支](https://devops.gitlab.cn/archives/81817)
2. 本地开发并频繁 commit（建议每个逻辑单位 commit）
3. push 到远程仓库（建议每日或每完成一阶段）
    ```shell
    git add .
    git commit -m "feat: 完成登录模块"
    git push origin feature/login-module
    ```
   如果推送被拒绝，解决冲突后再推送：
    ```shell
   git pull --rebase origin dev
   git push origin dev
    ```
4. 提 PR 合并回 dev 分支
5. 定期从 dev 合并测试稳定后再合并到 main

## 部署

首先运行

```shell
(sudo) git pull --rebase
```
创建.env文件
```shell
cd src
uv run sync-env
```

### 本地运行
api服务
```shell
cd src
uv run aiwen-api
```

mcp服务
```shell
cd src
uv run aiwen-mcp
```

### docker部署

```shell
# 记得设置项目名称
docker compose -p {project_name} up -d
```

~~### linux service部署~~
~~建议使用docker~~
~~一键部署~~

```shell
sudo bash install.sh
```