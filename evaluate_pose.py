from __future__ import absolute_import, division, print_function

import os
import torch
import models.encoders as encoders
import models.decoders as decoders
import numpy as np
from tqdm import tqdm

from torch.utils.data import DataLoader
from utils.layers import transformation_from_parameters
from utils.utils import readlines
from options import MonodepthOptions
from datasets import SCAREDRAWDataset
import scipy.stats as st


# from https://github.com/tinghuiz/SfMLearner
def dump_xyz(source_to_target_transformations):
    xyzs = []
    cam_to_world = np.eye(4)
    xyzs.append(cam_to_world[:3, 3])
    for source_to_target_transformation in source_to_target_transformations:
        cam_to_world = np.dot(cam_to_world, source_to_target_transformation)
        # cam_to_world = np.dot(source_to_target_transformation, cam_to_world)
        xyzs.append(cam_to_world[:3, 3])
    return xyzs


def dump_r(source_to_target_transformations):
    rs = []
    cam_to_world = np.eye(4)
    rs.append(cam_to_world[:3, :3])
    for source_to_target_transformation in source_to_target_transformations:
        cam_to_world = np.dot(cam_to_world, source_to_target_transformation)
        # cam_to_world = np.dot(source_to_target_transformation, cam_to_world)
        rs.append(cam_to_world[:3, :3])
    return rs


# from https://github.com/tinghuiz/SfMLearner
def compute_ate(gtruth_xyz, pred_xyz_o):
    # Make sure that the first matched frames align (no need for rotational alignment as
    # all the predicted/ground-truth snippets have been converted to use the same coordinate
    # system with the first frame of the snippet being the origin).
    offset = gtruth_xyz[0] - pred_xyz_o[0]
    pred_xyz = pred_xyz_o + offset[None, :]

    # Optimize the scaling factor
    scale = np.sum(gtruth_xyz * pred_xyz) / np.sum(pred_xyz ** 2)
    alignment_error = pred_xyz * scale - gtruth_xyz
    rmse = np.sqrt(np.sum(alignment_error ** 2)) / gtruth_xyz.shape[0]
    return rmse


def compute_re(gtruth_r, pred_r):
    RE = 0
    gt = gtruth_r
    pred = pred_r
    for gt_pose, pred_pose in zip(gt, pred):
        # Residual matrix to which we compute angle's sin and cos
        R = gt_pose @ np.linalg.inv(pred_pose)
        s = np.linalg.norm([R[0, 1] - R[1, 0],
                            R[1, 2] - R[2, 1],
                            R[0, 2] - R[2, 0]])
        c = np.trace(R) - 1
        # Note: we actually compute double of cos and sin, but arctan2 is invariant to scale
        RE += np.arctan2(s, c)

    return RE / gtruth_r.shape[0]


def evaluate(opt):
    """Evaluate odometry on the SCARED dataset
    """
    assert os.path.isdir(opt.load_weights_folder), \
        "Cannot find a folder at {}".format(opt.load_weights_folder)

    num_seq = [1,2,3,4]
    filenames = []
    for num in num_seq:
        frames = readlines(
        os.path.join(os.path.dirname(__file__), "splits", "endovis",
                     "test_files_sequence{}.txt".format(num)))
        filenames.append(frames)

    pose_encoder_path = os.path.join(opt.load_weights_folder, "pose_encoder.pth")
    pose_decoder_path = os.path.join(opt.load_weights_folder, "pose.pth")
    intrinsics_decoder_path = os.path.join(opt.load_weights_folder, "intrinsics_head.pth")

    pose_encoder = encoders.ResnetEncoder(opt.num_layers, False, 2)
    pose_encoder.load_state_dict(torch.load(pose_encoder_path))

    pose_decoder = decoders.PoseDecoder(pose_encoder.num_ch_enc, 1, 2)
    pose_decoder.load_state_dict(torch.load(pose_decoder_path))

    pose_encoder.cuda()
    pose_encoder.eval()
    pose_decoder.cuda()
    pose_decoder.eval()

    pred_poses = []
    opt.frame_ids = [0, 1]
    print("-> Computing pose predictions")
    with torch.no_grad():
        for i, num in enumerate(num_seq):
            dataset = SCAREDRAWDataset(opt.data_path, filenames[i], opt.height, opt.width,
                                    [0, 1], 4, is_train=False)
            dataloader = DataLoader(dataset, 1, shuffle=False,
                                 num_workers=opt.num_workers, pin_memory=True, drop_last=False)
            pred_poses_0 = []
            for inputs in tqdm(dataloader):
                for key, ipt in inputs.items():
                    inputs[key] = ipt.cuda()

                all_color_aug = torch.cat([inputs[("color", 1, 0)], inputs[("color", 0, 0)]], 1)

                features = [pose_encoder(all_color_aug)]
                axisangle, translation, intermediate_feature = pose_decoder(features)

                pred_poses_0.append(
                    transformation_from_parameters(axisangle[:, 0], translation[:, 0]).cpu().numpy())
                
            pred_poses.append(pred_poses_0)

    for i, num in enumerate(num_seq):
        pred_pos = np.concatenate(pred_poses[i])
        gt_path = os.path.join(os.path.dirname(__file__), "splits", "endovis", "trajectories", "gt", "gt_poses_sequence{}.npz".format(num))
        gt_local_poses = np.load(gt_path, fix_imports=True, encoding='latin1')["data"]
        pred_path = os.path.join(os.path.dirname(__file__), "splits", "endovis", "trajectories", opt.model_type, "pred_poses_sequence{}.npz".format(num))
        os.makedirs(pred_path, exist_ok=True)
        np.savez_compressed(pred_path, data=np.array(pred_pos))
        ates = []
        res = []
        num_frames = gt_local_poses.shape[0]
        track_length = 5
        for j in range(0, num_frames - 1):
            local_xyzs = np.array(dump_xyz(pred_pos[j:j + track_length - 1]))
            gt_local_xyzs = np.array(dump_xyz(gt_local_poses[j:j + track_length - 1]))
            local_rs = np.array(dump_r(pred_pos[j:j + track_length - 1]))
            gt_rs = np.array(dump_r(gt_local_poses[j:j + track_length - 1]))
            ates.append(compute_ate(gt_local_xyzs, local_xyzs))
            res.append(compute_re(local_rs, gt_rs))
        print("\n Absolute Trajectory Error of Seq. {}: {:0.4f}".format(i, np.mean(ates)))



if __name__ == "__main__":
    options = MonodepthOptions()
    evaluate(options.parse())
