"""
Explore Tools — list all available tools in the Crowe Logic Foundry.
"""
import importlib


def main():
    try:
        tools_init = importlib.import_module("tools")
        user_functions = getattr(tools_init, "user_functions", set())
        print(f"Crowe Logic Foundry has {len(user_functions)} tools:\n")
        for fn in sorted(user_functions, key=lambda f: f.__name__):
            doc = (fn.__doc__ or "").strip().split("\n")[0]
            print(f"  {fn.__name__:40s} {doc}")
    except ImportError:
        print("Tools module not available in sandbox mode.")
        print("This is a read-only demo. Full access requires admin privileges.")


if __name__ == "__main__":
    main()
