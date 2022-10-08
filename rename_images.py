import os
import argparse

# add preflix to the name of all images in a folder
def add_preflix(folder):
    for sub_dir in os.listdir(folder):
        sub_dir_path = os.path.join(folder, sub_dir)
        print(f"updating {sub_dir_path}...")
        for image in os.listdir(sub_dir_path):
            image_path = os.path.join(sub_dir_path, image)
            new_image_path = os.path.join(sub_dir_path, sub_dir + "-" + image)
            os.rename(image_path, new_image_path)
    print("done")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--folder", type=str, required=True, help="path to folder")
    args = parser.parse_args()
    add_preflix(args.folder)
