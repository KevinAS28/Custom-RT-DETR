import os
import sys
import argparse

import torch
import onnxruntime as ort
from PIL import Image, ImageDraw, ImageFont
from torchvision.transforms import ToTensor
import numpy as np

named_labels = {
    0: 'person',
    1: 'bicycle'
}

def inference_images(model_path, imgs_dir, output_dir, size=(640, 640)):
    if not os.path.isdir(output_dir):
        os.makedirs(output_dir)

    totensor = ToTensor()
    real_imgs = []
    for img_name in os.listdir(imgs_dir):
        img_path = os.path.join(imgs_dir, img_name)
        im = Image.open(img_path).convert('RGB')
        im = im.resize(size)
        real_imgs.append((img_name, im))


    imgs_preprocessed = torch.stack([totensor(i[1]) for i in real_imgs])
    sess = ort.InferenceSession(model_path)
    output = sess.run(
        # output_names=['labels', 'boxes', 'scores'],
        output_names=None,
        input_feed={'images': imgs_preprocessed.data.numpy(), "orig_target_sizes": torch.tensor([[640, 640]]*len(real_imgs)).data.numpy()}
    )

    labels, boxes, scores = output
    thrh = 0.6
    font = ImageFont.truetype('DejaVuSans.ttf', 16)

    for i in range(len(real_imgs)):
        print(f'{i+1}/{len(real_imgs)}')

        im = real_imgs[i][1]
        draw = ImageDraw.Draw(im)
        scr = scores[i]
        lab = labels[i][scr > thrh]
        box = boxes[i][scr > thrh]

        for b in box:
            draw.rectangle(list(b), outline='red',)
            draw.text((b[0], b[1]), text=f'{lab[i]} {named_labels[lab[i]]}', fill='blue', font=font)

        im.save(os.path.join(output_dir, real_imgs[i][0]))

def main(args):
    inference_images(args.model, args.imgs_dir, args.output_dir, (args.size, args.size))

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--model', '-m', type=str, )
    parser.add_argument('--imgs_dir', '-i', type=str, )
    parser.add_argument('--output_dir', '-o', type=str, )
    parser.add_argument('--size', '-s', type=int, default=640)

    args = parser.parse_args()

    main(args)