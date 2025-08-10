from setuptools import setup, find_packages

setup(
    name="tensorboard-plugin-cgs-dnn-analysis",
    version="1.0.0",
    description="A minimalist profiler plugin to display operator trees.",
    packages=find_packages(),
    package_data={
        "cgs_dnn_analysis": ["static/**"],
    },
    entry_points={
        "tensorboard_plugins": [
            "cgs-dnn-analysis = cgs_dnn_analysis.plugin:CGSDNNAnalysisPlugin",
        ],
    },
)
