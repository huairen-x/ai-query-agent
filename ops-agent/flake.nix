# ============================================================
# 智能运维 Agent - Nix Flake 构建
# 输出: Docker 镜像 tarball (result)
# 用法:
#   nix build .#dockerImage         # 构建 Docker 镜像
#   docker load < result             # 加载到本地 Docker
#   nix develop                      # 进入开发环境
# ============================================================
{
  description = "智能运维 Agent - AIOps for DolphinScheduler";

  inputs = {
    nixpkgs.url = "https://flakehub.com/f/DeterminateSystems/nixpkgs-weekly/0.1";
  };

  outputs = { self, nixpkgs }:
    let
      system = "x86_64-linux";
      pkgs = nixpkgs.legacyPackages.${system};

      # ----------------------------------------------------------
      # Python 基础环境（nixpkgs 标准包）
      # ----------------------------------------------------------
      pythonBase = pkgs.python311.withPackages (ps: with ps; [
        pyyaml
        requests
      ]);

      # ----------------------------------------------------------
      # 项目源码
      # ----------------------------------------------------------
      src = pkgs.lib.cleanSource ./.;

      # ----------------------------------------------------------
      # Docker 镜像
      # ----------------------------------------------------------
      dockerImage = pkgs.dockerTools.buildImage {
        name = "ops-agent";
        tag = "latest";
        created = "now";

        copyToRoot = pkgs.buildEnv {
          name = "image-root";
          paths = [
            pythonBase
            pkgs.dockerTools.binSh
            pkgs.coreutils
            pkgs.curl
            src
          ];
          pathsToLink = [ "/" "/app" ];
          postBuild = ''
            ln -sf ${pythonBase} "$out/usr/lib/python-env"
          '';
        };

        # 在镜像构建时安装 pip 包
        runAsRoot = ''
          #!${pkgs.runtimeShell}
          export PATH="${pythonBase}/bin:$PATH"
          export HOME=/tmp
          pip install --no-cache-dir openai flask 2>&1
        '';

        config = {
          Cmd = [ "--webhook" "--port" "8081" ];
          Entrypoint = [ "${pythonBase}/bin/python" "/app/main.py" ];
          ExposedPorts = { "8081/tcp" = { }; };
          Env = [
            "PYTHONPATH=/app"
            "PYTHONUNBUFFERED=1"
            "PYTHONDONTWRITEBYTECODE=1"
            "APP_MOCK_MODE=true"
            "APP_LOG_LEVEL=INFO"
          ];
          WorkingDir = "/app";
          User = "1001";
          Volumes = { "/app/data" = { }; };
        };
      };

    in {
      # ----------------------------------------------------------
      # 包输出
      # ----------------------------------------------------------
      packages.${system} = {
        default = dockerImage;
        dockerImage = dockerImage;
      };

      # ----------------------------------------------------------
      # 开发环境
      # ----------------------------------------------------------
      devShells.${system}.default = pkgs.mkShell {
        name = "ops-agent-dev";
        buildInputs = [
          pythonBase
          pkgs.python311Packages.pip
          pkgs.gcc
          pkgs.curl
        ];

        shellHook = ''
          echo "============================================"
          echo "  智能运维 Agent - 开发环境"
          echo "  Python: $(python --version)"
          echo "============================================"
          echo ""
          echo "  构建 Docker 镜像: nix build .#dockerImage"
          echo "  加载到 Docker:    docker load < result"
          echo "  运行测试:         python main.py --simulate all"
          echo ""
          export PYTHONPATH="$(pwd):$PYTHONPATH"
        '';
      };
    };
}